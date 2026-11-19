# audio_describer/ui/reset_dialog.py
"""Reset settings, presets and downloaded data (File > Reset...). Logic lives in models/data_reset."""
import wx

from ..i18n_setup import _
from ..models import data_reset
from ..utils.logger import app_logger


class ResetDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Reset Settings and Data"))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Choose what to reset. A backup copy of your settings and presets is "
                                               "saved first. Projects and exported files are never touched.")),
                  0, wx.ALL, 10)
        self.settings_cb = wx.CheckBox(self, label=_("&Settings (back to defaults)"))
        self.keys_cb = wx.CheckBox(self, label=_("Also forget my &API keys"))
        self.prompts_cb = wx.CheckBox(self, label=_("&Prompt presets (custom presets are removed)"))
        self.voices_cb = wx.CheckBox(self, label=_("OpenAI &voice presets"))
        self.downloads_cb = wx.CheckBox(self, label=_("&Downloaded voices and models (downloaded again when needed)"))
        self.temp_cb = wx.CheckBox(self, label=_("&Temporary files"))
        self.keys_cb.Disable()
        for cb in (self.settings_cb, self.keys_cb, self.prompts_cb, self.voices_cb, self.downloads_cb, self.temp_cb):
            sizer.Add(cb, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.settings_cb.Bind(wx.EVT_CHECKBOX, lambda e: self.keys_cb.Enable(self.settings_cb.IsChecked()))
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel(_("&Reset"))
        sizer.Add(buttons, 0, wx.ALL | wx.EXPAND, 10)
        self.SetSizerAndFit(sizer)
        self.settings_cb.SetFocus()
        self.Bind(wx.EVT_BUTTON, self.on_reset, id=wx.ID_OK)

    def on_reset(self, event):
        chosen = [cb for cb in (self.settings_cb, self.prompts_cb, self.voices_cb, self.downloads_cb, self.temp_cb)
                  if cb.IsChecked()]
        if not chosen:
            wx.MessageBox(_("Nothing is selected."), _("Reset"), wx.OK | wx.ICON_INFORMATION, self)
            return
        if wx.MessageBox(_("Reset the selected items now? This cannot be undone from the app."), _("Confirm Reset"),
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) != wx.YES:
            return
        try:
            if self.settings_cb.IsChecked():
                data_reset.reset_settings(keep_api_keys=not self.keys_cb.IsChecked())
            if self.prompts_cb.IsChecked():
                data_reset.reset_prompts()
            if self.voices_cb.IsChecked():
                data_reset.reset_voices()
            if self.downloads_cb.IsChecked():
                data_reset.clear_downloaded_data()
            if self.temp_cb.IsChecked():
                data_reset.clear_temporary_files()
        except Exception as e:
            app_logger.error(f"Reset failed: {e}", exc_info=True)
            wx.MessageBox(_("The reset did not complete: %s") % e, _("Reset"), wx.OK | wx.ICON_ERROR, self)
            return
        self.EndModal(wx.ID_OK)
