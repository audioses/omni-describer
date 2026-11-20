# audio_describer/ui/glossary_dialog.py
from audio_describer.i18n_setup import _
import wx
from ..core import timing


def _voice_choices():
    """(stored value, label) for a character's voice when reading its translated lines."""
    return [(None, _("Automatic (speaker's apparent gender on each line)")), ("female", _("Woman's voice")),
            ("male", _("Man's voice")), ("narrator", _("Description voice"))]


def _voice_label(voice):
    return dict(_voice_choices()).get(voice, _voice_choices()[0][1])


class GlossaryDialog(wx.Dialog):
    """Characters and the voice that reads each one's translated dialogue (voice casting).
    Works on copies; the caller keeps .glossary and .descriptions only on OK."""

    def __init__(self, parent, glossary, descriptions):
        super().__init__(parent, title=_("Character Voices"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.glossary = [dict(g) for g in (glossary or []) if isinstance(g, dict)]
        self.descriptions = list(descriptions or [])

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("&Characters (Enter changes the voice of the selected one):")), 0, wx.ALL, 5)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for col, (label, width) in enumerate(((_("Name"), 150), (_("Description"), 350), (_("Voice"), 220))):
            self.list.InsertColumn(col, label, width=width)
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 5)
        voice_button = wx.Button(self, label=_("Change &Voice..."))
        voice_button.Bind(wx.EVT_BUTTON, self.OnChangeVoice)
        sizer.Add(voice_button, 0, wx.ALL, 5)
        sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizerAndFit(sizer)
        self.SetMinSize((520, 320))
        self.CentreOnParent()

        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnChangeVoice)
        self._refresh(0)
        self.list.SetFocus()

    def _refresh(self, select):
        self.list.DeleteAllItems()
        for i, g in enumerate(self.glossary):
            self.list.InsertItem(i, str(g.get("name") or g.get("id") or ""))
            self.list.SetItem(i, 1, str(g.get("description") or ""))
            self.list.SetItem(i, 2, _voice_label(g.get("voice")))
        if self.glossary:
            select = max(0, min(select, len(self.glossary) - 1))
            self.list.Select(select)
            self.list.Focus(select)

    def OnChangeVoice(self, event):
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        entry = self.glossary[row]
        choices = _voice_choices()
        values = [v for v, _l in choices]
        # The AI's view of the speaker is shown but never overwritten (apparent_gender).
        seen = {"female": _("woman"), "male": _("man")}.get(entry.get("apparent_gender"), _("unknown"))
        with wx.SingleChoiceDialog(self, _("Voice for the translated lines of %(name)s (the AI saw: %(seen)s):")
                                   % {"name": entry.get("name") or entry.get("id"), "seen": seen},
                                   _("Change Voice"), [label for _v, label in choices]) as dlg:
            dlg.SetSelection(values.index(entry.get("voice")) if entry.get("voice") in values else 0)
            if dlg.ShowModal() != wx.ID_OK:
                return
            entry["voice"] = values[dlg.GetSelection()]
        self.descriptions, _changed = timing.apply_voice_casting(self.descriptions, self.glossary)
        self._refresh(row)
