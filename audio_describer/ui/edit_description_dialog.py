# audio_describer/ui/edit_description_dialog.py
from audio_describer.i18n_setup import _
import wx
import sys
import copy
from audio_describer.utils.logger import app_logger
from audio_describer.core import timing

class EditDescriptionDialog(wx.Dialog):
    def __init__(self, parent, descriptions_list, video_duration, character_glossary=None, selected_original_index=0, title=_("Edit Audio Descriptions"), seek_callback=None):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        # --- FIX: Work on a deep copy to ensure transactional changes ---
        self.all_descriptions = copy.deepcopy(descriptions_list)
        self.video_duration = video_duration
        self.character_glossary = character_glossary if character_glossary is not None else []
        self.current_edit_original_index = -1
        self.current_duration_sec = 0.0
        self._seek_callback = seek_callback
 
        panel = wx.Panel(self)
        main_panel_sizer = wx.BoxSizer(wx.VERTICAL)
 
        self.notebook = wx.Notebook(panel)
        
        desc_panel = wx.Panel(self.notebook)
        desc_sizer = wx.BoxSizer(wx.VERTICAL)

        combo_label = wx.StaticText(desc_panel, label=_("Select description to edit:"))
        desc_sizer.Add(combo_label, 0, wx.ALL | wx.EXPAND, 5)

        self.desc_combo = wx.ComboBox(desc_panel, style=wx.CB_READONLY, name="DescriptionSelectorCombo")
        desc_sizer.Add(self.desc_combo, 0, wx.ALL | wx.EXPAND, 5)

        edit_group_box = wx.StaticBox(desc_panel, label=_("Details"))
        edit_sizer = wx.StaticBoxSizer(edit_group_box, wx.VERTICAL)
        
        grid_sizer = wx.FlexGridSizer(3, 2, 5, 5)
        grid_sizer.AddGrowableCol(1, 1)

        grid_sizer.Add(wx.StaticText(edit_group_box, label=_("Start Time (sec):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
        self.start_time_ctrl = wx.TextCtrl(edit_group_box)
        grid_sizer.Add(self.start_time_ctrl, 1, wx.EXPAND | wx.ALL, 2)

        grid_sizer.Add(wx.StaticText(edit_group_box, label=_("End Time (sec):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
        self.end_time_ctrl = wx.TextCtrl(edit_group_box)
        grid_sizer.Add(self.end_time_ctrl, 1, wx.EXPAND | wx.ALL, 2)

        grid_sizer.Add(wx.StaticText(edit_group_box, label=_("Description Text:")), 0, wx.TOP, 5)
        self.edit_text_ctrl = wx.TextCtrl(edit_group_box, style=wx.TE_MULTILINE, size=(-1, 100))
        grid_sizer.Add(self.edit_text_ctrl, 1, wx.EXPAND | wx.ALL, 2)
        grid_sizer.AddGrowableRow(2, 1)

        edit_sizer.Add(grid_sizer, 1, wx.EXPAND | wx.ALL, 5)
        desc_sizer.Add(edit_sizer, 1, wx.EXPAND | wx.ALL, 5)
        
        desc_panel.SetSizer(desc_sizer)
        self.notebook.AddPage(desc_panel, _("Descriptions"))
 
        if self.character_glossary:
            glossary_panel = wx.Panel(self.notebook)
            glossary_sizer = wx.BoxSizer(wx.VERTICAL)
            
            self.glossary_list_ctrl = wx.ListCtrl(glossary_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
            self.glossary_list_ctrl.InsertColumn(0, _("ID"), width=150)
            self.glossary_list_ctrl.InsertColumn(1, _("Name"), width=150)
            self.glossary_list_ctrl.InsertColumn(2, _("Description"), width=400)
            
            for item in self.character_glossary:
                index = self.glossary_list_ctrl.GetItemCount()
                # Ensure all values are strings to prevent TypeErrors
                item_id = str(item.get('id', 'N/A'))
                item_name = str(item.get('name', _('Not named')))
                item_desc = str(item.get('description', 'N/A'))
                
                self.glossary_list_ctrl.InsertItem(index, item_id)
                self.glossary_list_ctrl.SetItem(index, 1, item_name)
                self.glossary_list_ctrl.SetItem(index, 2, item_desc)

            glossary_sizer.Add(self.glossary_list_ctrl, 1, wx.EXPAND | wx.ALL, 5)
            glossary_panel.SetSizer(glossary_sizer)
            self.notebook.AddPage(glossary_panel, _("Characters"))
 
        main_panel_sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(main_panel_sizer)

        dialog_button_sizer = wx.StdDialogButtonSizer()
        self.add_button = wx.Button(self, label=_("&Add New..."))
        self.delete_button = wx.Button(self, label=_("&Delete"))
        # --- FIX: Changed ID_OK to a regular button to prevent accidental closing ---
        self.save_button = wx.Button(self, label=_("&Save Changes"))
        self.close_button = wx.Button(self, id=wx.ID_OK, label=_("Close")) # OK button now just closes
        
        dialog_button_sizer.AddButton(self.add_button)
        dialog_button_sizer.AddButton(self.delete_button)
        dialog_button_sizer.AddStretchSpacer()
        dialog_button_sizer.AddButton(self.save_button)
        dialog_button_sizer.AddButton(self.close_button)
        dialog_button_sizer.Realize()

        dialog_main_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_main_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 5)
        dialog_main_sizer.Add(dialog_button_sizer, 0, wx.EXPAND | wx.ALL, 10)

        self.SetSizer(dialog_main_sizer)
        self.Fit()
        self.SetMinSize((500, 450))
        self.CentreOnParent()

        self.desc_combo.Bind(wx.EVT_COMBOBOX, self.OnDescriptionSelect)
        self.start_time_ctrl.Bind(wx.EVT_KILL_FOCUS, self.OnStartTimeChanged)
        self.save_button.Bind(wx.EVT_BUTTON, self.OnSave)
        self.delete_button.Bind(wx.EVT_BUTTON, self.OnDelete)
        self.add_button.Bind(wx.EVT_BUTTON, self.OnAddNew)
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.OnPageChanged)

        # Ctrl+S accelerator for Save
        save_accel_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self.OnSave, id=save_accel_id)
        accel_tbl = wx.AcceleratorTable([
            (wx.ACCEL_CTRL, ord('S'), save_accel_id),
        ])
        self.SetAcceleratorTable(accel_tbl)
 
        self.populate_combo_box()

        # If a specific index is provided, select it. Otherwise, select the first item.
        if selected_original_index is not None and selected_original_index < len(self.all_descriptions):
            combo_index_to_select = self.find_combo_item_by_original_index(selected_original_index)
        else:
            combo_index_to_select = 0 if self.all_descriptions else wx.NOT_FOUND

        if combo_index_to_select != wx.NOT_FOUND:
            self.desc_combo.SetSelection(combo_index_to_select)
        
        self.update_edit_fields_from_combo()
        self.OnPageChanged(None) # Set initial state

    def OnPageChanged(self, event):
        is_desc_page = self.notebook.GetSelection() == 0
        self.add_button.Enable(is_desc_page)
        self.delete_button.Enable(is_desc_page)
        self.save_button.Enable(is_desc_page)
        if event:
            event.Skip()

    def GetUpdatedDescriptions(self):
        return self.all_descriptions

    def populate_combo_box(self):
        self.desc_combo.Clear()
        if not self.all_descriptions:
            self.desc_combo.Append(_("(No descriptions available)"), clientData=-1)
            self.desc_combo.SetSelection(0)
            return

        for i, (start, end, text) in enumerate(self.all_descriptions):
            preview_text = text.replace('\n', ' ').strip()[:40] + "..."
            display_string = f"[{start:.2f}s] {preview_text}"
            self.desc_combo.Append(display_string, clientData=i)

    def find_combo_item_by_original_index(self, original_idx):
        for i in range(self.desc_combo.GetCount()):
            if self.desc_combo.GetClientData(i) == original_idx:
                return i
        return wx.NOT_FOUND

    def update_edit_fields_from_combo(self):
        sel_idx = self.desc_combo.GetSelection()
        is_enabled = sel_idx != wx.NOT_FOUND and bool(self.all_descriptions)
        
        self.start_time_ctrl.Enable(is_enabled)
        self.end_time_ctrl.Enable(is_enabled)
        self.edit_text_ctrl.Enable(is_enabled)
        self.save_button.Enable(is_enabled)
        self.delete_button.Enable(is_enabled)

        if not is_enabled:
            self.start_time_ctrl.SetValue("")
            self.end_time_ctrl.SetValue("")
            self.edit_text_ctrl.SetValue("")
            self.current_edit_original_index = -1
            self.current_duration_sec = 0.0
            return

        original_idx = self.desc_combo.GetClientData(sel_idx)
        self.current_edit_original_index = original_idx
        start, end, text = self.all_descriptions[original_idx]
        
        self.current_duration_sec = end - start
        self.start_time_ctrl.SetValue(f"{start:.3f}")
        self.end_time_ctrl.SetValue(f"{end:.3f}")
        self.edit_text_ctrl.SetValue(text)

    def OnDescriptionSelect(self, event):
        self.update_edit_fields_from_combo()
        self._seek_to_current_description()

    def _seek_to_current_description(self):
        """Seeks the player to the start time of the currently selected description."""
        if self._seek_callback and self.current_edit_original_index >= 0:
            start, _, _ = self.all_descriptions[self.current_edit_original_index]
            try:
                self._seek_callback(start)
            except Exception as e:
                app_logger.error(f"Seek callback failed: {e}")

    def OnStartTimeChanged(self, event):
        event.Skip()
        try:
            new_start = float(self.start_time_ctrl.GetValue())
            if not (0 <= new_start <= self.video_duration):
                raise ValueError(_("Start time must be within the video duration (0 to %.2f sec).") % self.video_duration)
            
            new_end = new_start + self.current_duration_sec
            self.end_time_ctrl.SetValue(f"{new_end:.3f}")

        except (ValueError, TypeError) as e:
            wx.MessageBox(str(e) or _("Invalid start time. Please enter a valid number."), _("Invalid Input"), wx.OK | wx.ICON_ERROR, self)
            start, _, _ = self.all_descriptions[self.current_edit_original_index]
            self.start_time_ctrl.SetValue(f"{start:.3f}")

    def OnSave(self, event):
        if self.current_edit_original_index == -1: return
        try:
            new_start = float(self.start_time_ctrl.GetValue())
            new_end = float(self.end_time_ctrl.GetValue())
            new_text = self.edit_text_ctrl.GetValue()

            if not (0 <= new_start < new_end <= self.video_duration):
                raise ValueError(_("Invalid time range. Times must be within the video duration (0 to %.2f sec) and end time must be after start time.") % self.video_duration)
            if not new_text.strip():
                raise ValueError(_("Description text cannot be empty."))

            # --- FIX: Update the local list directly ---
            old = self.all_descriptions[self.current_edit_original_index]  # keep its speaker and voice flags
            self.all_descriptions[self.current_edit_original_index] = timing.Description(new_start, new_end, new_text, timing.flags_of(old))
            self.all_descriptions.sort(key=lambda x: x[0])
            
            # Repopulate to reflect changes and sorting
            current_idx = self.current_edit_original_index
            self.populate_combo_box()
            self.desc_combo.SetSelection(self.find_combo_item_by_original_index(current_idx))
            self.update_edit_fields_from_combo()

            wx.MessageBox(_("Changes saved."), _("Success"), wx.OK | wx.ICON_INFORMATION, self)

        except (ValueError, TypeError) as e:
            wx.MessageBox(str(e), _("Validation Error"), wx.OK | wx.ICON_ERROR, self)

    def OnDelete(self, event):
        sel_idx = self.desc_combo.GetSelection()
        if sel_idx == -1 or not self.all_descriptions: return
        
        original_idx = self.desc_combo.GetClientData(sel_idx)
        if wx.MessageBox(_("Are you sure you want to delete this description?"), _("Confirm Delete"), wx.YES_NO | wx.ICON_WARNING, self) == wx.YES:
            del self.all_descriptions[original_idx]
            
            # Reselect a reasonable item
            new_sel = min(sel_idx, len(self.all_descriptions) - 1)
            self.populate_combo_box()
            if new_sel >= 0:
                self.desc_combo.SetSelection(new_sel)
            self.update_edit_fields_from_combo()

    def OnAddNew(self, event):
        with AddDescriptionDialog(self, self.video_duration) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                new_desc = dlg.GetData()
                if new_desc is None: return
                    
                self.all_descriptions.append(new_desc)
                self.all_descriptions.sort(key=lambda x: x[0])
                new_idx = self.all_descriptions.index(new_desc)

                self.populate_combo_box()
                self.desc_combo.SetSelection(self.find_combo_item_by_original_index(new_idx))
                self.update_edit_fields_from_combo()

class AddDescriptionDialog(wx.Dialog):
    def __init__(self, parent, video_duration):
        super().__init__(parent, title=_("Add New Description"), style=wx.DEFAULT_DIALOG_STYLE)
        self.video_duration = video_duration
        
        panel = wx.Panel(self)
        sizer = wx.FlexGridSizer(3, 2, 5, 5)
        sizer.AddGrowableCol(1, 1)

        sizer.Add(wx.StaticText(panel, label=_("Start Time (sec):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.start_ctrl = wx.TextCtrl(panel, value="0.0")
        sizer.Add(self.start_ctrl, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(panel, label=_("End Time (sec):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.end_ctrl = wx.TextCtrl(panel, value="5.0")
        sizer.Add(self.end_ctrl, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(panel, label=_("Description Text:")), 0, wx.TOP, 3)
        self.text_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 80))
        sizer.Add(self.text_ctrl, 1, wx.EXPAND)
        sizer.AddGrowableRow(2, 1)

        panel.SetSizer(sizer)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(panel, 1, wx.EXPAND | wx.ALL, 10)
        
        btn_sizer = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizerAndFit(main_sizer)
        self.CentreOnParent()
        
        self.Bind(wx.EVT_BUTTON, self.OnOK, id=wx.ID_OK)

    def OnOK(self, event):
        if self.GetData() is not None:
            self.EndModal(wx.ID_OK)

    def GetData(self):
        try:
            start = float(self.start_ctrl.GetValue())
            end = float(self.end_ctrl.GetValue())
            text = self.text_ctrl.GetValue().strip()

            if not text:
                raise ValueError(_("Description text cannot be empty."))
            # --- FIX: Validate against video duration ---
            if not (0 <= start < end <= self.video_duration):
                raise ValueError(_("Invalid time range. Times must be within the video duration (0 to %.2f sec) and end time must be after start time.") % self.video_duration)
            
            return (start, end, text)

        except (ValueError, TypeError) as e:
            wx.MessageBox(str(e) or _("Please enter valid numbers for times."), _("Invalid Input"), wx.OK | wx.ICON_ERROR, self)
            return None