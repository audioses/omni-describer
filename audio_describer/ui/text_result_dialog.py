# audio_describer/ui/text_result_dialog.py
"""Read-only result of an image or document description; read by screen readers and braille."""
from ..i18n_setup import _
import wx


class TextResultDialog(wx.Dialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, title=_("Description: %s") % title, size=(640, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("&Description:")), 0, wx.LEFT | wx.TOP, 5)
        self.text_ctrl = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY)
        sizer.Add(self.text_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        copy_button = wx.Button(self, label=_("&Copy"))
        copy_button.Bind(wx.EVT_BUTTON, self.OnCopy)
        buttons.Add(copy_button, 0, wx.ALL, 5)
        buttons.Add(wx.Button(self, wx.ID_CLOSE), 0, wx.ALL, 5)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.text_ctrl.SetFocus()

    def OnCopy(self, event):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(self.text_ctrl.GetValue()))
            wx.TheClipboard.Close()
