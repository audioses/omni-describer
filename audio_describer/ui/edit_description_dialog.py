# audio_describer/ui/edit_description_dialog.py
"""Description editor (audit §3.2, §6.4, P1.6): a list with one row per description,
times as HH:MM:SS.mmm, keyboard shortcuts for timing against the player, review navigation,
undo/redo. Works on a copy; the player takes the list back when the dialog closes."""
from audio_describer.i18n_setup import _
import wx
import os
import tempfile
import threading
from ..core import timing, tts_engine
from ..utils import sound_player
from .glossary_dialog import GlossaryDialog
from .accessibility_utils import speak_message
from audio_describer.utils.logger import app_logger

MAX_UNDO = 100
NUDGE_SEC = 1.0


def _flag_labels():
    return {timing.FLAG_OVER_BUDGET: _("too long"), timing.FLAG_OVERLAPS_SOUND: _("over sound"),
            timing.FLAG_UNCERTAIN: _("uncertain, check with Ask More"),
            timing.FLAG_TRANSLATION: _("translated dialogue"),
            timing.FLAG_VOICE_FEMALE: _("female voice"), timing.FLAG_VOICE_MALE: _("male voice"),
            timing.FLAG_PAST_TENSE: _("style: not in the present tense"),
            timing.FLAG_INTERPRETATION: _("style: interprets instead of describing"),
            timing.FLAG_OUT_OF_FICTION: _("style: leaves the fiction (actors, camera)")}


def _parse_time(text, video_duration):
    """HH:MM:SS.mmm, MM:SS or plain seconds -> float, within the video; ValueError otherwise."""
    try:
        value = timing.parse_timestamp(text.strip())
    except ValueError:
        raise ValueError(_("Invalid time \"%s\". Use HH:MM:SS.mmm, MM:SS or seconds.") % text.strip())
    if value > video_duration:
        raise ValueError(_("Times must be within the video duration (0 to %s).") % timing.format_vtt_time(video_duration))
    return value


class EditDescriptionDialog(wx.Dialog):
    def __init__(self, parent, descriptions_list, video_duration, character_glossary=None, selected_original_index=0,
                 title=_("Edit Audio Descriptions"), seek_callback=None, position_callback=None, play_pause_callback=None):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.all_descriptions = list(descriptions_list)  # Description objects are immutable: a shallow copy is enough
        self.video_duration = video_duration
        self.character_glossary = [dict(g) for g in (character_glossary or [])]  # copy: kept only on close
        self._seek_callback = seek_callback
        self._position_callback = position_callback
        self._play_pause_callback = play_pause_callback
        self.current = -1           # index in all_descriptions shown in the fields
        self._loading = False       # True while the code (not the user) changes the selection or fields
        self._undo, self._redo = [], []

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_("Descriptions &list:")), 0, wx.LEFT | wx.TOP, 5)
        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(760, 260), name="DescriptionList")
        for col, (label, width) in enumerate(((_("Start"), 95), (_("End"), 95), (_("Text"), 330),
                                               (_("Words/s"), 65), (_("Status"), 150), (_("Level"), 50))):
            self.list.InsertColumn(col, label, width=width)
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 5)

        box = wx.StaticBox(panel, label=_("Details"))
        details = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid = wx.FlexGridSizer(5, 2, 5, 5)
        grid.AddGrowableCol(1, 1)

        def time_row(label, button_label):
            grid.Add(wx.StaticText(box, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
            row = wx.BoxSizer(wx.HORIZONTAL)
            ctrl = wx.TextCtrl(box)
            button = wx.Button(box, label=button_label)
            button.Enable(position_callback is not None)
            row.Add(ctrl, 1, wx.EXPAND | wx.RIGHT, 5)
            row.Add(button, 0)
            grid.Add(row, 1, wx.EXPAND | wx.ALL, 2)
            return ctrl, button

        self.start_time_ctrl, start_button = time_row(_("Start (HH:MM:SS.mmm):"), _("S&tart = player position (Ctrl+[)"))
        self.end_time_ctrl, end_button = time_row(_("End (HH:MM:SS.mmm):"), _("E&nd = player position (Ctrl+])"))

        grid.Add(wx.StaticText(box, label=_("Description Text:")), 0, wx.TOP, 5)
        self.edit_text_ctrl = wx.TextCtrl(box, style=wx.TE_MULTILINE, size=(-1, 90))
        grid.Add(self.edit_text_ctrl, 1, wx.EXPAND | wx.ALL, 2)
        grid.AddGrowableRow(2, 1)
        # Reviewer workflow (audit §3.5): status and remark per description, exported in the production script.
        grid.Add(wx.StaticText(box, label=_("Review status:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
        self.status_choice = wx.Choice(box, choices=[_("To review"), _("Validated")])
        grid.Add(self.status_choice, 1, wx.EXPAND | wx.ALL, 2)
        grid.Add(wx.StaticText(box, label=_("Reviewer notes:")), 0, wx.TOP, 5)
        self.note_ctrl = wx.TextCtrl(box, style=wx.TE_MULTILINE, size=(-1, 45))
        grid.Add(self.note_ctrl, 1, wx.EXPAND | wx.ALL, 2)
        details.Add(grid, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(details, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(wx.StaticText(panel, label=_(
            "F8 / Shift+F8: next / previous description to review. Ctrl+P: play or pause the video. "
            "Alt+Left / Alt+Right: move the video 1 second. Ctrl+Z / Ctrl+Y in the list: undo / redo.")),
            0, wx.ALL, 5)
        panel.SetSizer(sizer)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.add_button = wx.Button(self, label=_("&Add New..."))
        self.delete_button = wx.Button(self, label=_("&Delete"))
        # Character glossary in its own window (add, edit, delete, rename), even when empty.
        self.characters_button = wx.Button(self, label=_("C&haracters..."))
        self.listen_button = wx.Button(self, label=_("Hea&r with the export voice (Ctrl+R)"))
        self.save_button = wx.Button(self, label=_("&Save Changes"))
        self.close_button = wx.Button(self, id=wx.ID_OK, label=_("Close"))
        for b in (self.add_button, self.delete_button, self.characters_button, self.listen_button):
            buttons.Add(b, 0, wx.RIGHT, 5)
        buttons.AddStretchSpacer()
        buttons.Add(self.save_button, 0, wx.RIGHT, 5)
        buttons.Add(self.close_button, 0)

        main = wx.BoxSizer(wx.VERTICAL)
        main.Add(panel, 1, wx.EXPAND | wx.ALL, 5)
        main.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(main)
        self.Fit()
        self.SetMinSize((640, 520))
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_OK)  # Escape and the title-bar close go through OnClose: nothing is lost silently

        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnDescriptionSelect)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self.edit_text_ctrl.SetFocus())
        self.start_time_ctrl.Bind(wx.EVT_KILL_FOCUS, self.OnStartTimeChanged)
        start_button.Bind(wx.EVT_BUTTON, lambda e: self._set_time_from_player(self.start_time_ctrl))
        end_button.Bind(wx.EVT_BUTTON, lambda e: self._set_time_from_player(self.end_time_ctrl))
        self.save_button.Bind(wx.EVT_BUTTON, self.OnSave)
        self.delete_button.Bind(wx.EVT_BUTTON, self.OnDelete)
        self.add_button.Bind(wx.EVT_BUTTON, self.OnAddNew)
        self.characters_button.Bind(wx.EVT_BUTTON, self.OnCharacters)
        self.listen_button.Bind(wx.EVT_BUTTON, self.OnListen)
        self.close_button.Bind(wx.EVT_BUTTON, self.OnClose)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_CHAR_HOOK, self.OnCharHook)

        self._refresh_list()
        if self.all_descriptions:
            index = selected_original_index if 0 <= (selected_original_index or 0) < len(self.all_descriptions) else 0
            self._select(index or 0)
        else:
            self._load_fields(-1)
        self.list.SetFocus()

    # ----- list -----------------------------------------------------------------------------
    def _status_text(self, desc):
        labels = _flag_labels()
        warnings = [labels[f] for f in timing.flags_of(desc) if f in labels]
        parts = [_("Validated")] if timing.FLAG_VALIDATED in timing.flags_of(desc) else []
        if warnings:
            parts.append(_("Review: %s") % ", ".join(warnings))
        return ". ".join(parts)

    def _refresh_list(self):
        self._loading = True
        try:
            self.list.DeleteAllItems()
            for i, desc in enumerate(self.all_descriptions):
                start, end, text = desc
                self.list.InsertItem(i, timing.format_vtt_time(start))
                self.list.SetItem(i, 1, timing.format_vtt_time(end))
                self.list.SetItem(i, 2, text.replace("\n", " ").strip())
                self.list.SetItem(i, 3, f"{len(text.split()) / max(0.1, end - start):.1f}")
                self.list.SetItem(i, 4, self._status_text(desc))
                self.list.SetItem(i, 5, str(timing.level_of(desc)))
        finally:
            self._loading = False

    def _select(self, index):
        """Select a row from code (no seek, no unsaved-changes check) and load it."""
        self._loading = True
        try:
            selected = self.list.GetFirstSelected()
            if selected != -1 and selected != index:
                self.list.Select(selected, False)
            if 0 <= index < self.list.GetItemCount():
                self.list.Select(index)
                self.list.Focus(index)
                self.list.EnsureVisible(index)
        finally:
            self._loading = False
        self._load_fields(index)

    def _load_fields(self, index):
        enabled = 0 <= index < len(self.all_descriptions)
        for ctrl in (self.start_time_ctrl, self.end_time_ctrl, self.edit_text_ctrl, self.status_choice,
                     self.note_ctrl, self.save_button, self.delete_button):
            ctrl.Enable(enabled)
        self.current = index if enabled else -1
        self._loading = True
        try:
            if not enabled:
                for ctrl in (self.start_time_ctrl, self.end_time_ctrl, self.edit_text_ctrl, self.note_ctrl):
                    ctrl.SetValue("")
                return
            desc = self.all_descriptions[index]
            self.start_time_ctrl.SetValue(timing.format_vtt_time(desc[0]))
            self.end_time_ctrl.SetValue(timing.format_vtt_time(desc[1]))
            self.edit_text_ctrl.SetValue(desc[2])
            self.status_choice.SetSelection(1 if timing.FLAG_VALIDATED in timing.flags_of(desc) else 0)
            self.note_ctrl.SetValue(timing.note_of(desc))
        finally:
            self._loading = False

    def OnDescriptionSelect(self, event):
        if self._loading:
            return
        index = event.GetIndex()
        if index == self.current:
            return
        previous, target = self.current, self.all_descriptions[index]
        if not self._apply_edits(announce=False):  # invalid pending edit: stay on it
            wx.CallAfter(self._select, previous)
            return
        # applying may re-sort the list: find the row the user moved to by identity
        self._select(next(i for i, d in enumerate(self.all_descriptions) if d is target))
        self._seek_to_current_description()

    def _seek_to_current_description(self):
        if self._seek_callback and self.current >= 0:
            try:
                self._seek_callback(self.all_descriptions[self.current][0])
            except Exception as e:
                app_logger.error(f"Seek callback failed: {e}")

    # ----- editing --------------------------------------------------------------------------
    def _has_pending_edits(self):
        if self.current < 0:
            return False
        desc = self.all_descriptions[self.current]
        return (self.start_time_ctrl.GetValue().strip() != timing.format_vtt_time(desc[0])
                or self.end_time_ctrl.GetValue().strip() != timing.format_vtt_time(desc[1])
                or self.edit_text_ctrl.GetValue() != desc[2]
                or (self.status_choice.GetSelection() == 1) != (timing.FLAG_VALIDATED in timing.flags_of(desc))
                or self.note_ctrl.GetValue().strip() != timing.note_of(desc))

    def _push_undo(self):
        self._undo.append((list(self.all_descriptions), [dict(g) for g in self.character_glossary], self.current))
        del self._undo[:-MAX_UNDO]
        self._redo.clear()

    def _apply_edits(self, announce=True):
        """Write the fields into the list. True if there was nothing to apply or it worked."""
        if not self._has_pending_edits():
            return True
        try:
            new_start = _parse_time(self.start_time_ctrl.GetValue(), self.video_duration)
            new_end = _parse_time(self.end_time_ctrl.GetValue(), self.video_duration)
            new_text = self.edit_text_ctrl.GetValue()
            if new_start >= new_end:
                raise ValueError(_("The end time must be after the start time."))
            if not new_text.strip():
                raise ValueError(_("Description text cannot be empty."))
        except ValueError as e:
            wx.MessageBox(str(e), _("Validation Error"), wx.OK | wx.ICON_ERROR, self)
            return False
        self._push_undo()
        old = self.all_descriptions[self.current]
        old_flags = timing.flags_of(old)
        flags = list(timing.compute_flags(new_start, new_end, new_text,
                                          on_screen_text=timing.FLAG_ON_SCREEN_TEXT in old_flags,
                                          translation=timing.FLAG_TRANSLATION in old_flags,
                                          gender=timing.line_gender(old)))
        flags += timing.speaker_flags(old)
        if new_text == old[2]:  # unchanged text keeps the AI reviewer's remarks
            flags += [f for f in old_flags if f in timing.STYLE_FLAGS + (timing.FLAG_UNCERTAIN,)]
        if self.status_choice.GetSelection() == 1:
            flags.append(timing.FLAG_VALIDATED)
        updated = timing.Description(new_start, new_end, new_text, timing.level_of(old), flags,
                                     self.note_ctrl.GetValue().strip())
        self.all_descriptions[self.current] = updated
        self.all_descriptions.sort(key=lambda d: d[0])
        self._refresh_list()
        self._select(next(i for i, d in enumerate(self.all_descriptions) if d is updated))
        if announce:
            speak_message(_("Changes saved."), interrupt=True)
        return True

    def OnSave(self, event):
        if self.current >= 0 and not self._has_pending_edits():
            speak_message(_("No changes to save."), interrupt=True)
            return
        self._apply_edits()

    def OnStartTimeChanged(self, event):
        """Moving the start keeps the duration: the end follows."""
        event.Skip()
        if self._loading or self.current < 0:
            return
        desc = self.all_descriptions[self.current]
        try:
            new_start = _parse_time(self.start_time_ctrl.GetValue(), self.video_duration)
        except ValueError:
            return  # reported when the edit is applied
        if abs(new_start - desc[0]) > 0.0005:
            self.end_time_ctrl.SetValue(timing.format_vtt_time(min(self.video_duration, new_start + desc[1] - desc[0])))

    def _set_time_from_player(self, ctrl):
        if self.current < 0 or not self._position_callback:
            return
        position = max(0.0, min(self.video_duration, self._position_callback()))
        ctrl.SetValue(timing.format_vtt_time(position))
        if ctrl is self.start_time_ctrl:
            desc = self.all_descriptions[self.current]
            self.end_time_ctrl.SetValue(timing.format_vtt_time(min(self.video_duration, position + desc[1] - desc[0])))
        speak_message(timing.format_vtt_time(position), interrupt=True)

    def OnDelete(self, event):
        if self.current < 0:
            return
        if wx.MessageBox(_("Are you sure you want to delete this description?"), _("Confirm Delete"),
                         wx.YES_NO | wx.ICON_WARNING, self) != wx.YES:
            return
        self._push_undo()
        index = self.current
        del self.all_descriptions[index]
        self._refresh_list()
        self._select(min(index, len(self.all_descriptions) - 1))
        self.list.SetFocus()

    def OnAddNew(self, event):
        if not self._apply_edits(announce=False):
            return
        position = self._position_callback() if self._position_callback else 0.0
        with AddDescriptionDialog(self, self.video_duration, position) as dlg:
            if dlg.ShowModal() != wx.ID_OK or dlg.GetData() is None:
                return
            start, end, text = dlg.GetData()
        self._push_undo()
        new_desc = timing.Description(start, end, text, flags=timing.compute_flags(start, end, text))
        self.all_descriptions.append(new_desc)
        self.all_descriptions.sort(key=lambda d: d[0])
        self._refresh_list()
        self._select(next(i for i, d in enumerate(self.all_descriptions) if d is new_desc))
        self.list.SetFocus()

    def OnCharacters(self, event):
        if not self._apply_edits(announce=False):
            return
        with GlossaryDialog(self, self.character_glossary, self.all_descriptions) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self._push_undo()
            self.character_glossary = dlg.glossary
            if dlg.renamed:
                self.all_descriptions = list(dlg.descriptions)
                self._refresh_list()
                self._select(self.current)

    def _undo_redo(self, source, target, done_message):
        if not source:
            speak_message(_("Nothing to undo.") if source is self._undo else _("Nothing to redo."), interrupt=True)
            return
        target.append((list(self.all_descriptions), [dict(g) for g in self.character_glossary], self.current))
        self.all_descriptions, self.character_glossary, index = source.pop()
        self._refresh_list()
        self._select(min(max(index, 0), len(self.all_descriptions) - 1))
        speak_message(done_message, interrupt=True)

    def _next_to_review(self, step):
        if not self._apply_edits(announce=False):
            return
        current = self.current if self.current >= 0 else (-1 if step > 0 else len(self.all_descriptions))
        index = timing.next_to_review(self.all_descriptions, current, step)
        if index is None:
            speak_message(_("No description left to review."), interrupt=True)
            return
        self._select(index)
        self.list.SetFocus()
        self._seek_to_current_description()

    def _nudge_player(self, delta):
        if self._seek_callback and self._position_callback:
            position = max(0.0, min(self.video_duration, self._position_callback() + delta))
            self._seek_callback(position)
            speak_message(timing.format_vtt_time(position), interrupt=True)

    def OnCharHook(self, event):
        key = event.GetKeyCode()
        char = event.GetUnicodeKey()
        ctrl, shift, alt = event.ControlDown(), event.ShiftDown(), event.AltDown()
        in_text = isinstance(self.FindFocus(), wx.TextCtrl)
        if key == wx.WXK_F8 and not ctrl and not alt:
            self._next_to_review(-1 if shift else 1)
        elif ctrl and not alt and (char in (ord("["), ord("]")) or key in (ord("["), ord("]"))):
            self._set_time_from_player(self.start_time_ctrl if ord("[") in (char, key) else self.end_time_ctrl)
        elif ctrl and not alt and not shift and key == ord("S"):
            self.OnSave(None)
        elif ctrl and not alt and not shift and key == ord("R"):
            self.OnListen(None)
        elif ctrl and not alt and not shift and key == ord("G"):
            self._cycle_voice_gender()
        elif ctrl and not alt and not shift and key == ord("P") and self._play_pause_callback:
            self._play_pause_callback()
        elif alt and not ctrl and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            self._nudge_player(NUDGE_SEC if key == wx.WXK_RIGHT else -NUDGE_SEC)
        elif ctrl and not alt and not in_text and key == ord("Z"):
            self._undo_redo(self._undo, self._redo, _("Undone."))
        elif ctrl and not alt and not in_text and key == ord("Y"):
            self._undo_redo(self._redo, self._undo, _("Redone."))
        elif key == wx.WXK_DELETE and self.FindFocus() is self.list:
            self.OnDelete(None)
        else:
            event.Skip()

    def _cycle_voice_gender(self):
        """Ctrl+G on translated dialogue: speaker's voice unknown -> female -> male -> unknown."""
        if self.current < 0 or not self._apply_edits(announce=False):
            return
        old = self.all_descriptions[self.current]
        if timing.FLAG_TRANSLATION not in timing.flags_of(old):
            speak_message(_("Only translated dialogue has a speaker's voice."), interrupt=True)
            return
        order = [None, "female", "male"]
        gender = order[(order.index(timing.line_gender(old)) + 1) % len(order)]
        self._push_undo()
        flags = [f for f in timing.flags_of(old) if f not in timing.GENDER_FLAGS.values()]
        if gender:
            flags.append(timing.GENDER_FLAGS[gender])
        self.all_descriptions[self.current] = timing.Description(old[0], old[1], old[2], timing.level_of(old),
                                                                 flags, timing.note_of(old))
        self._refresh_list()
        self._select(self.current)
        speak_message({None: _("Speaker's voice: unknown"), "female": _("Speaker's voice: female"),
                       "male": _("Speaker's voice: male")}[gender], interrupt=True)

    def OnListen(self, event):
        """Hear the text as the export will say it (TTS engine + pronunciation dictionary)."""
        text = self.edit_text_ctrl.GetValue().strip()
        if not text:
            return
        self.listen_button.Disable()
        path = os.path.join(tempfile.gettempdir(), "omni_describer_listen.wav")
        gender = timing.voice_gender(self.all_descriptions[self.current]) if self.current >= 0 else None

        def work():
            try:
                ok = tts_engine.synthesize_description_to_wav(text, path, gender=gender)
            except Exception as e:
                app_logger.warning(f"Listen: synthesis failed: {e}")
                ok = False
            wx.CallAfter(done, ok)

        def done(ok):
            if not self:
                return
            self.listen_button.Enable()
            if not (ok and sound_player.play_voice(path)):
                speak_message(_("The export voice could not be played. Check the voice in Settings."), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

    def OnClose(self, event):
        if self._has_pending_edits():
            answer = wx.MessageBox(_("The current description has unsaved changes. Save them before closing?"),
                                   _("Unsaved Changes"), wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
            if answer == wx.CANCEL:
                if isinstance(event, wx.CloseEvent) and event.CanVeto():
                    event.Veto()
                return
            if answer == wx.YES and not self._apply_edits(announce=False):
                if isinstance(event, wx.CloseEvent) and event.CanVeto():
                    event.Veto()
                return
        self.EndModal(wx.ID_OK)  # saved changes are always kept, whichever way the dialog closes

    def GetUpdatedDescriptions(self):
        return self.all_descriptions


class AddDescriptionDialog(wx.Dialog):
    def __init__(self, parent, video_duration, start_sec=0.0):
        super().__init__(parent, title=_("Add New Description"), style=wx.DEFAULT_DIALOG_STYLE)
        self.video_duration = video_duration
        start_sec = max(0.0, min(start_sec, max(0.0, video_duration - 0.1)))
        panel = wx.Panel(self)
        sizer = wx.FlexGridSizer(3, 2, 5, 5)
        sizer.AddGrowableCol(1, 1)

        sizer.Add(wx.StaticText(panel, label=_("Start (HH:MM:SS.mmm):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.start_ctrl = wx.TextCtrl(panel, value=timing.format_vtt_time(start_sec))
        sizer.Add(self.start_ctrl, 1, wx.EXPAND)
        sizer.Add(wx.StaticText(panel, label=_("End (HH:MM:SS.mmm):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.end_ctrl = wx.TextCtrl(panel, value=timing.format_vtt_time(min(video_duration, start_sec + 3.0)))
        sizer.Add(self.end_ctrl, 1, wx.EXPAND)
        sizer.Add(wx.StaticText(panel, label=_("Description Text:")), 0, wx.TOP, 3)
        self.text_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 80))
        sizer.Add(self.text_ctrl, 1, wx.EXPAND)
        sizer.AddGrowableRow(2, 1)
        panel.SetSizer(sizer)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizerAndFit(main_sizer)
        self.CentreOnParent()
        self.text_ctrl.SetFocus()
        self.Bind(wx.EVT_BUTTON, self.OnOK, id=wx.ID_OK)
        self._data = None

    def OnOK(self, event):
        try:
            start = _parse_time(self.start_ctrl.GetValue(), self.video_duration)
            end = _parse_time(self.end_ctrl.GetValue(), self.video_duration)
            text = self.text_ctrl.GetValue().strip()
            if not text:
                raise ValueError(_("Description text cannot be empty."))
            if start >= end:
                raise ValueError(_("The end time must be after the start time."))
        except ValueError as e:
            wx.MessageBox(str(e), _("Invalid Input"), wx.OK | wx.ICON_ERROR, self)
            return
        self._data = (start, end, text)
        self.EndModal(wx.ID_OK)

    def GetData(self):
        return self._data
