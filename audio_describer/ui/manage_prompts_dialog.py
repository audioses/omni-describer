# audio_describer/ui/manage_prompts_dialog.py
from ..i18n_setup import _
import json
import wx
import wx.lib.mixins.listctrl as listmix

from ..models import prompt_model, config_model
from .add_edit_prompt_dialog import AddEditPromptDialog
from .accessibility_utils import set_control_accessible_name

class ManagePromptsDialog(wx.Dialog, listmix.ListCtrlAutoWidthMixin):
    def __init__(self, parent):
        super().__init__(parent, title=_("Manage Prompt Presets"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, size=(600, 400))
        listmix.ListCtrlAutoWidthMixin.__init__(self)

        self.current_lang = config_model.ui_language()
        self.prompts = prompt_model.get_prompts_for_language(self.current_lang)

        self.InitUI()
        self.PopulateList()
        self.CentreOnParent()

    def InitUI(self):
        # A top-level sizer for the dialog itself
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # A panel for the main content area
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        # Info text (child of panel, added to panel's sizer)
        info_text = wx.StaticText(panel, label=_("Manage presets for the '%s' language.") % self.current_lang)
        panel_sizer.Add(info_text, 0, wx.ALL | wx.EXPAND, 5)

        # List control for prompts (child of panel, added to panel's sizer)
        self.prompt_list_ctrl = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.prompt_list_ctrl.InsertColumn(0, _("Preset Name"), width=150)
        self.prompt_list_ctrl.InsertColumn(1, _("Prompt Text"), width=350)
        set_control_accessible_name(self.prompt_list_ctrl, _("List of saved prompt presets."))
        self.setResizeColumn(1)
        panel_sizer.Add(self.prompt_list_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        
        panel.SetSizer(panel_sizer)
        
        # Add the content panel to the dialog's main sizer
        dialog_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 0)

        # Button sizer and buttons (children of the dialog `self`)
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.add_button = wx.Button(self, label=_("&Add..."))
        self.edit_button = wx.Button(self, label=_("&Edit..."))
        self.delete_button = wx.Button(self, label=_("&Delete"))
        self.export_button = wx.Button(self, label=_("E&xport..."))
        self.import_button = wx.Button(self, label=_("&Import..."))
        self.close_button = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
        
        self.edit_button.Disable()
        self.delete_button.Disable()
        
        button_sizer.Add(self.add_button, 0, wx.ALL, 5)
        button_sizer.Add(self.edit_button, 0, wx.ALL, 5)
        button_sizer.Add(self.delete_button, 0, wx.ALL, 5)
        button_sizer.Add(self.export_button, 0, wx.ALL, 5)
        button_sizer.Add(self.import_button, 0, wx.ALL, 5)
        button_sizer.AddStretchSpacer()
        button_sizer.Add(self.close_button, 0, wx.ALL, 5)
        
        # Add the button sizer to the dialog's main sizer
        dialog_sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        self.SetSizerAndFit(dialog_sizer)

        # Bind events
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnItemSelected, self.prompt_list_ctrl)
        self.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.OnItemDeselected, self.prompt_list_ctrl)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnEdit, self.prompt_list_ctrl)
        self.add_button.Bind(wx.EVT_BUTTON, self.OnAdd)
        self.edit_button.Bind(wx.EVT_BUTTON, self.OnEdit)
        self.delete_button.Bind(wx.EVT_BUTTON, self.OnDelete)
        self.export_button.Bind(wx.EVT_BUTTON, self.OnExport)
        self.import_button.Bind(wx.EVT_BUTTON, self.OnImport)
        # wx.ID_CANCEL is handled automatically by the dialog, no need to bind self.Close()

    def PopulateList(self):
        self.prompt_list_ctrl.DeleteAllItems()
        for i, prompt in enumerate(self.prompts):
            index = self.prompt_list_ctrl.InsertItem(i, prompt["name"])
            self.prompt_list_ctrl.SetItem(index, 1, prompt["prompt"])
            self.prompt_list_ctrl.SetItemData(index, i)

        self.OnItemDeselected(None)

    def OnItemSelected(self, event):
        self.edit_button.Enable()
        self.delete_button.Enable()

    def OnItemDeselected(self, event):
        self.edit_button.Disable()
        self.delete_button.Disable()

    def OnAdd(self, event):
        with AddEditPromptDialog(self, title=_("Add New Prompt")) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                new_data = dlg.GetPromptData()
                self.prompts.append(new_data)
                self.SaveChanges()
                self.PopulateList()
                self.prompt_list_ctrl.SetItemState(self.prompt_list_ctrl.GetItemCount() - 1, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

    def OnEdit(self, event):
        selected_idx = self.prompt_list_ctrl.GetFirstSelected()
        if selected_idx == -1: return
        original_idx = self.prompt_list_ctrl.GetItemData(selected_idx)
        prompt_to_edit = self.prompts[original_idx]
        with AddEditPromptDialog(self, title=_("Edit Prompt"), prompt_name=prompt_to_edit["name"], prompt_text=prompt_to_edit["prompt"]) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                updated_data = dlg.GetPromptData()
                self.prompts[original_idx] = updated_data
                self.SaveChanges()
                self.PopulateList()
                self.prompt_list_ctrl.SetItemState(selected_idx, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

    def OnDelete(self, event):
        selected_idx = self.prompt_list_ctrl.GetFirstSelected()
        if selected_idx == -1: return
        original_idx = self.prompt_list_ctrl.GetItemData(selected_idx)
        prompt_name = self.prompts[original_idx]["name"]
        msg = wx.MessageDialog(self, _("Are you sure you want to delete the preset '%s'?") % prompt_name, _("Confirm Delete"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if msg.ShowModal() == wx.ID_YES:
            del self.prompts[original_idx]
            self.SaveChanges()
            self.PopulateList()
        msg.Destroy()

    def OnExport(self, event):
        """Share presets with other describers (a JSON list of {name, prompt})."""
        with wx.FileDialog(self, _("Export Presets"), defaultFile="presets.json", wildcard="JSON (*.json)|*.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.prompts, f, indent=2, ensure_ascii=False)
        except OSError as e:
            wx.MessageBox(_("Could not save the file:\n%s") % e, _("Error"), wx.OK | wx.ICON_ERROR, self)

    def OnImport(self, event):
        with wx.FileDialog(self, _("Import Presets"), wildcard="JSON (*.json)|*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            imported = prompt_model.validate_presets(data)
        except (OSError, ValueError) as e:
            wx.MessageBox(_("This file does not contain valid presets:\n%s") % e, _("Error"), wx.OK | wx.ICON_ERROR, self)
            return
        by_name = {p["name"]: i for i, p in enumerate(self.prompts)}
        for preset in imported:  # same name: the imported version replaces ours
            if preset["name"] in by_name:
                self.prompts[by_name[preset["name"]]] = preset
            else:
                self.prompts.append(preset)
        self.SaveChanges()
        self.PopulateList()
        wx.MessageBox(_("%d presets imported.") % len(imported), _("Import Presets"), wx.OK | wx.ICON_INFORMATION, self)

    def SaveChanges(self):
        prompt_model.set_prompts_for_language(self.current_lang, self.prompts)