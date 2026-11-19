# audio_describer/ui/glossary_dialog.py
from audio_describer.i18n_setup import _
import wx
from ..core import timing


def _voice_choices():
    """(stored value, label) for a character's voice when reading its translated lines."""
    return [(None, _("Automatic (speaker's apparent gender on each line)")), ("female", _("Woman's voice")),
            ("male", _("Man's voice")), ("narrator", _("Description voice"))]


def _gender_label(gender):
    return {"female": _("woman"), "male": _("man")}.get(gender, _("unknown"))


class GlossaryDialog(wx.Dialog):
    """Character glossary: add, edit, delete. Renaming a character also renames it
    in the descriptions; a character's voice is applied to its translated lines.
    Works on copies; the caller keeps .glossary and .descriptions only on OK."""

    def __init__(self, parent, glossary, descriptions):
        super().__init__(parent, title=_("Characters"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.glossary = [dict(g) for g in (glossary or []) if isinstance(g, dict)]
        self.descriptions = list(descriptions or [])
        self.renamed = 0  # descriptions changed (renames, voice casting): the caller keeps them

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("&Characters kept consistent across the video:")), 0, wx.ALL, 5)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, name=_("Characters"))
        for col, (label, width) in enumerate(((_("ID"), 120), (_("Name"), 150), (_("Description"), 400), (_("Voice"), 160))):
            self.list.InsertColumn(col, label, width=width)
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 5)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in ((_("&Add..."), self.OnAdd), (_("&Edit..."), self.OnEdit), (_("&Delete"), self.OnDelete)):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 5)
        sizer.Add(row, 0, wx.ALL, 5)
        sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizerAndFit(sizer)
        self.SetMinSize((520, 360))
        self.CentreOnParent()

        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnEdit)  # Enter edits
        self.list.Bind(wx.EVT_KEY_DOWN, self.OnListKey)          # Delete key deletes
        self._refresh(0)
        self.list.SetFocus()

    def _refresh(self, select=None):
        self.list.DeleteAllItems()
        for i, g in enumerate(self.glossary):
            self.list.InsertItem(i, str(g.get("id") or ""))
            self.list.SetItem(i, 1, str(g.get("name") or _("Not named")))
            self.list.SetItem(i, 2, str(g.get("description") or ""))
            self.list.SetItem(i, 3, dict(_voice_choices()).get(g.get("voice"), _voice_choices()[0][1]))
        if self.glossary and select is not None:
            select = max(0, min(select, len(self.glossary) - 1))
            self.list.Select(select)
            self.list.Focus(select)

    def _recast(self):
        self.descriptions, changed = timing.apply_voice_casting(self.descriptions, self.glossary)
        self.renamed += changed

    def _selected(self):
        row = self.list.GetFirstSelected()
        if row == -1:
            wx.MessageBox(_("Select a character first."), _("Characters"), wx.OK | wx.ICON_INFORMATION, self)
            return None
        return row

    def _ask(self, entry, title, exclude=None):
        """Shows the entry form until it is valid or cancelled. Returns the new entry or None."""
        with CharacterEntryDialog(self, title, entry) as dlg:
            while dlg.ShowModal() == wx.ID_OK:
                new = {**entry, **dlg.GetEntry()}  # keeps any field the form doesn't show
                taken = {g.get("id") for i, g in enumerate(self.glossary) if i != exclude}
                if not new["id"] or not new["description"]:
                    error = _("ID and description are required.")
                elif new["id"] in taken:
                    error = _("Another character already uses this ID.")
                else:
                    return new
                wx.MessageBox(error, title, wx.OK | wx.ICON_ERROR, dlg)
        return None

    def OnAdd(self, event):
        taken = {g.get("id") for g in self.glossary}
        n = len(self.glossary) + 1
        while f"character_{n}" in taken:
            n += 1
        entry = self._ask({"id": f"character_{n}", "name": None, "description": ""}, _("Add Character"))
        if entry:
            self.glossary.append(entry)
            self._recast()
            self._refresh(len(self.glossary) - 1)

    def OnEdit(self, event):
        row = self._selected()
        if row is None:
            return
        old = self.glossary[row]
        entry = self._ask(old, _("Edit Character"), exclude=row)
        if not entry:
            return
        self.glossary[row] = entry
        old_name, new_name = (old.get("name") or "").strip(), entry.get("name") or ""
        changed = 0
        if old_name and new_name and new_name != old_name:
            self.descriptions, changed = timing.rename_in_descriptions(self.descriptions, old_name, new_name)
            self.renamed += changed
        self._recast()
        self._refresh(row)
        if changed:
            wx.MessageBox(_("Character renamed. %d descriptions updated.") % changed, _("Edit Character"),
                          wx.OK | wx.ICON_INFORMATION, self)

    def OnDelete(self, event):
        row = self._selected()
        if row is None:
            return
        if wx.MessageBox(_("Delete this character from the glossary?"), _("Confirm Delete"),
                         wx.YES_NO | wx.ICON_WARNING, self) == wx.YES:
            del self.glossary[row]
            self._recast()
            self._refresh(row)

    def OnListKey(self, event):
        if event.GetKeyCode() == wx.WXK_DELETE:
            self.OnDelete(event)
        else:
            event.Skip()


class CharacterEntryDialog(wx.Dialog):
    def __init__(self, parent, title, entry):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        grid = wx.FlexGridSizer(4, 2, 5, 5)
        grid.AddGrowableCol(1, 1)
        grid.Add(wx.StaticText(self, label=_("&ID:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.id_ctrl = wx.TextCtrl(self, value=str(entry.get("id") or ""))
        grid.Add(self.id_ctrl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Name (leave empty if not revealed yet):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.name_ctrl = wx.TextCtrl(self, value=str(entry.get("name") or ""))
        grid.Add(self.name_ctrl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Description (how to recognise them):")), 0, wx.TOP, 3)
        self.desc_ctrl = wx.TextCtrl(self, value=str(entry.get("description") or ""), style=wx.TE_MULTILINE, size=(-1, 80))
        grid.Add(self.desc_ctrl, 1, wx.EXPAND)
        grid.AddGrowableRow(2, 1)
        # Voice override, independent of the gender the AI saw (apparent_gender is never overwritten).
        voice_label = _("&Voice for this character's translated lines (AI saw: %s):") % _gender_label(entry.get("apparent_gender"))
        grid.Add(wx.StaticText(self, label=voice_label), 0, wx.ALIGN_CENTER_VERTICAL)
        self._voices = _voice_choices()
        self.voice_ctrl = wx.Choice(self, choices=[label for _v, label in self._voices], name=voice_label.replace("&", ""))
        values = [v for v, _l in self._voices]
        self.voice_ctrl.SetSelection(values.index(entry.get("voice")) if entry.get("voice") in values else 0)
        grid.Add(self.voice_ctrl, 1, wx.EXPAND)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizerAndFit(sizer)
        self.SetMinSize((420, 260))
        self.CentreOnParent()
        # New character: start on the description; existing one: on the name (the usual fix).
        (self.name_ctrl if entry.get("description") else self.desc_ctrl).SetFocus()

    def GetEntry(self):
        return {"id": self.id_ctrl.GetValue().strip(),
                "name": self.name_ctrl.GetValue().strip() or None,
                "description": self.desc_ctrl.GetValue().strip(),
                "voice": self._voices[max(0, self.voice_ctrl.GetSelection())][0]}
