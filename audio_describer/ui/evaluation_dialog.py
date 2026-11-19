# audio_describer/ui/evaluation_dialog.py
"""Keyboard-friendly CSA 2020 questionnaire: two tabs (global listening, analysis), one
choice per question whose label carries the automatic measurement, a comment box per phase."""
from ..i18n_setup import _
import wx

from ..models import evaluation_model as ev


class EvaluationDialog(wx.Dialog):
    def __init__(self, parent, title, descriptions, duration=None, saved=None):
        super().__init__(parent, title=_("Evaluate this Audio Description"), size=(720, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        saved = saved or {}
        self.title = title
        self.hints = ev.auto_hints(descriptions, duration)
        rating_labels = [_("not rated"), _("1, very poor"), _("2, poor"), _("3, fair"), _("4, good"), _("5, very good")]
        self.choices, self.comment_ctrls = {}, {}

        notebook = wx.Notebook(self)
        for phase, name in ((ev.GLOBAL, _("Global listening, without the picture")),
                            (ev.ANALYTIC, _("Analysis, with the picture"))):
            page = wx.ScrolledWindow(notebook)
            page.SetScrollRate(0, 20)
            sizer = wx.BoxSizer(wx.VERTICAL)
            for key, p, question in ev.questions():
                if p != phase:
                    continue
                label = question + (f" ({self.hints[key]})" if key in self.hints else "")
                sizer.Add(wx.StaticText(page, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
                choice = wx.Choice(page, choices=rating_labels)
                choice.SetName(label)  # screen readers announce the question with the control
                choice.SetSelection(int(saved.get("ratings", {}).get(key) or 0))
                sizer.Add(choice, 0, wx.EXPAND | wx.ALL, 5)
                self.choices[key] = choice
            sizer.Add(wx.StaticText(page, label=_("&Comments:")), 0, wx.LEFT | wx.TOP, 5)
            comment = wx.TextCtrl(page, value=saved.get("comments", {}).get(phase, ""), style=wx.TE_MULTILINE, size=(-1, 100))
            sizer.Add(comment, 0, wx.EXPAND | wx.ALL, 5)
            self.comment_ctrls[phase] = comment
            page.SetSizer(sizer)
            notebook.AddPage(page, name)

        main = wx.BoxSizer(wx.VERTICAL)
        main.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        save_report = wx.Button(self, label=_("Save &Report..."))
        save_report.Bind(wx.EVT_BUTTON, self.OnSaveReport)
        buttons.Add(save_report, 0, wx.ALL, 5)
        buttons.AddStretchSpacer()
        buttons.Add(wx.Button(self, wx.ID_OK), 0, wx.ALL, 5)
        buttons.Add(wx.Button(self, wx.ID_CANCEL), 0, wx.ALL, 5)
        main.Add(buttons, 0, wx.EXPAND)
        self.SetSizer(main)

    def GetValues(self):
        ratings = {k: c.GetSelection() for k, c in self.choices.items() if c.GetSelection() > 0}
        comments = {p: c.GetValue() for p, c in self.comment_ctrls.items()}
        return {"ratings": ratings, "comments": comments}

    def GetReport(self):
        values = self.GetValues()
        return ev.build_report(self.title, values["ratings"], values["comments"], self.hints)

    def OnSaveReport(self, event):
        with wx.FileDialog(self, _("Save Evaluation Report"), wildcard=_("Text files (*.txt)|*.txt"),
                           defaultFile=f"{self.title}_AD_evaluation.txt", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.GetReport())
        except OSError as e:
            wx.MessageBox(_("Error saving file: %s") % e, _("Export Error"), wx.OK | wx.ICON_ERROR, self)
