# audio_describer/ui/license_dialog.py
import wx
import os
from ..i18n_setup import _
from .. import config
from ..models import config_model
from ..utils.logger import app_logger

class LicenseDialog(wx.Dialog):
    def __init__(self, parent, view_only=False):
        super().__init__(parent, title=_("License Agreement"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.view_only = view_only

        # --- Corrected Sizer and Panel Layout ---
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        # --- Load License Text ---
        license_text = self._load_license_text()

        # --- Widgets (all are children of `panel`) ---
        title_text = wx.StaticText(panel, label=_("License and Terms of Use"))
        font = title_text.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title_text.SetFont(font)
        panel_sizer.Add(title_text, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)

        # Informational text for view-only mode
        if self.view_only:
            info_label = wx.StaticText(panel, label=_("You have already agreed to these terms."))
            panel_sizer.Add(info_label, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL, 10)

        self.license_text_ctrl = wx.TextCtrl(panel, value=license_text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.license_text_ctrl.SetMinSize((500, 300))
        panel_sizer.Add(self.license_text_ctrl, 1, wx.EXPAND | wx.ALL, 10)
        
        panel.SetSizer(panel_sizer)
        dialog_sizer.Add(panel, 1, wx.EXPAND)

        # --- Buttons (children of `self`, the dialog) ---
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        if self.view_only:
            # Only show an OK button in view-only mode
            self.ok_button = wx.Button(self, wx.ID_OK, label=_("OK"))
            self.ok_button.SetDefault()
            button_sizer.Add(self.ok_button, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        else:
            # Show Agree/Disagree for the initial agreement
            self.agree_button = wx.Button(self, wx.ID_OK, label=_("Agree"))
            self.disagree_button = wx.Button(self, wx.ID_CANCEL, label=_("Disagree"))
            button_sizer.Add(self.disagree_button, 0, wx.ALL, 5)
            button_sizer.AddStretchSpacer(1)
            button_sizer.Add(self.agree_button, 0, wx.ALL, 5)
            self.agree_button.SetDefault()

        dialog_sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        self.SetSizerAndFit(dialog_sizer)
        self.CentreOnParent()

        # Bind events based on mode
        if self.view_only:
            self.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_OK), id=wx.ID_OK)
        else:
            self.Bind(wx.EVT_BUTTON, self.on_agree, id=wx.ID_OK)
            self.Bind(wx.EVT_BUTTON, self.on_disagree, id=wx.ID_CANCEL)

    def _load_license_text(self):
        filename = "license.txt"
        not_found_msg = _("License file could not be found. Please contact support.")
        try:
            doc_dir = config.get_doc_dir()
            lang_code = config_model.ui_language() or 'en'
            
            lang_specific_file = os.path.join(doc_dir, lang_code, filename)
            default_file = os.path.join(doc_dir, 'en', filename)
            
            file_to_open = lang_specific_file if os.path.exists(lang_specific_file) else default_file
            
            if os.path.exists(file_to_open):
                with open(file_to_open, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                app_logger.error(f"License file not found. Checked: {file_to_open}")
                return not_found_msg
        except Exception as e:
            app_logger.error(f"Error loading license file: {e}", exc_info=True)
            return f"{not_found_msg}\n\nError: {e}"

    def on_agree(self, event):
        self.EndModal(wx.ID_OK)

    def on_disagree(self, event):
        self.EndModal(wx.ID_CANCEL)