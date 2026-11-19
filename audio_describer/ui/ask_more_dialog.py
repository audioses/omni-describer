# audio_describer/ui/ask_more_dialog.py
from ..i18n_setup import _
import wx

from .accessibility_utils import speak_message, set_control_accessible_name
from ..utils.logger import app_logger

EVT_ASK_MORE_SUBMIT_ID = wx.NewIdRef()
EVT_ASK_MORE_SUBMIT = wx.PyEventBinder(EVT_ASK_MORE_SUBMIT_ID, 0)
EVT_ASK_MORE_ADD_TO_MAIN_ID = wx.NewIdRef()
EVT_ASK_MORE_ADD_TO_MAIN = wx.PyEventBinder(EVT_ASK_MORE_ADD_TO_MAIN_ID, 0)

class AskMoreSubmitEvent(wx.PyCommandEvent):
    def __init__(self, eventType=EVT_ASK_MORE_SUBMIT_ID, id=0):
        super().__init__(eventType, id)
        self.user_question = ""
        self.context_duration_sec = 0

    def GetQuestion(self): return self.user_question
    def SetQuestion(self, question): self.user_question = question
    def GetContextDuration(self): return self.context_duration_sec
    def SetContextDuration(self, duration): self.context_duration_sec = duration

class AskMoreAddToMainEvent(wx.PyCommandEvent):
    def __init__(self, eventType=EVT_ASK_MORE_ADD_TO_MAIN_ID, id=0):
        super().__init__(eventType, id)
        self.text_to_add = ""

    def GetText(self): return self.text_to_add
    def SetText(self, text): self.text_to_add = text

class AskMoreDialog(wx.Dialog):
    def __init__(self, parent, current_video_time_sec, title=_("Ask More About Scene")):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.FRAME_FLOAT_ON_PARENT | wx.FRAME_NO_TASKBAR | wx.STAY_ON_TOP)

        self.initial_video_time_sec = current_video_time_sec
        self.last_ai_answer = ""
        self.last_response_is_error = False # NEW: State flag

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        info_text = wx.StaticText(self.panel, label=_("Ask questions about the scene around %s.") % f"{self.initial_video_time_sec:.1f}s")
        panel_sizer.Add(info_text, 0, wx.ALL | wx.EXPAND, 10)

        history_label = wx.StaticText(self.panel, label=_("Conversation History:"))
        panel_sizer.Add(history_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        self.history_text_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_STATIC, size=(-1, 150))
        panel_sizer.Add(self.history_text_ctrl, 2, wx.EXPAND | wx.ALL, 5)

        question_label = wx.StaticText(self.panel, label=_("Your New Question:"))
        panel_sizer.Add(question_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        self.question_text_ctrl = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER, size=(-1, 40))
        panel_sizer.Add(self.question_text_ctrl, 0, wx.EXPAND | wx.ALL, 5)

        duration_sizer = wx.BoxSizer(wx.HORIZONTAL)
        duration_label = wx.StaticText(self.panel, label=_("Video Context Duration (seconds):"))
        duration_sizer.Add(duration_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.duration_spin_ctrl = wx.SpinCtrl(self.panel, value="5", min=1, max=60, initial=5)
        duration_sizer.Add(self.duration_spin_ctrl, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        panel_sizer.Add(duration_sizer, 0, wx.ALL | wx.ALIGN_LEFT, 5)

        self.panel.SetSizer(panel_sizer)
        dialog_sizer.Add(self.panel, 1, wx.EXPAND)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.submit_button = wx.Button(self, wx.ID_OK, label=_("&Submit Question"))
        self.submit_button.SetDefault()
        button_sizer.Add(self.submit_button, 0, wx.ALL, 5)

        self.add_to_main_button = wx.Button(self, label=_("&Add to Descriptions"))
        self.add_to_main_button.Disable()
        button_sizer.Add(self.add_to_main_button, 0, wx.ALL, 5)

        self.close_button = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
        button_sizer.Add(self.close_button, 0, wx.ALL, 5)
        
        dialog_sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, 10)

        self.SetSizerAndFit(dialog_sizer)
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()

        self.Bind(wx.EVT_BUTTON, self.OnSubmit, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self.OnAddToMain, self.add_to_main_button)
        self.question_text_ctrl.SetFocus()
        self.exchanges = []

    def AskNow(self, question):
        """Submit a question programmatically (W key: what is on screen)."""
        self.question_text_ctrl.SetValue(question)
        self.OnSubmit(None)

    def OnSubmit(self, event):
        user_question = self.question_text_ctrl.GetValue().strip()
        if not user_question:
            wx.MessageBox(_("Please enter a question."), _("Question Required"), wx.OK | wx.ICON_WARNING, self); return

        self.OnProcessingStart()
        self._pending_question = user_question
        self.AppendToHistory(_("You asked: %s") % user_question)

        submit_event = AskMoreSubmitEvent()
        submit_event.SetQuestion(user_question)
        submit_event.SetContextDuration(float(self.duration_spin_ctrl.GetValue()))
        wx.PostEvent(self.GetParent(), submit_event)
        self.question_text_ctrl.Clear()

    def OnAddToMain(self, event):
        if not self.last_ai_answer or self.last_response_is_error: return
        add_event = AskMoreAddToMainEvent()
        add_event.SetText(self.last_ai_answer)
        wx.PostEvent(self.GetParent(), add_event)
        self.add_to_main_button.Disable()
        wx.MessageBox(_("The description has been added to the main list."), _("Description Added"), wx.OK | wx.ICON_INFORMATION, self)

    def AppendToHistory(self, text):
        if self.history_text_ctrl.GetValue(): self.history_text_ctrl.AppendText("\n" + "-"*30 + "\n")
        self.history_text_ctrl.AppendText(text + "\n")
        self.history_text_ctrl.ShowPosition(self.history_text_ctrl.GetLastPosition())

    def OnProcessingStart(self):
        self.submit_button.Disable()
        self.add_to_main_button.Disable()
        self.last_ai_answer = ""
        self.last_response_is_error = True # Assume error until success
        self.AppendToHistory(_("AI is thinking..."))

    def SetAnswer(self, answer_text, is_error=False):
        self.last_response_is_error = is_error
        self.last_ai_answer = answer_text
        if not is_error and getattr(self, "_pending_question", None):
            self.exchanges.append((self._pending_question, answer_text))  # memory for follow-up questions
        self._pending_question = None
        
        prefix = _("AI Answer: ")
        if is_error:
            prefix = _("Error: ")

        self.AppendToHistory(prefix + answer_text)
        speak_message(prefix + answer_text, interrupt=True)
        
        self.submit_button.Enable()
        # Only enable the 'Add' button if it was a successful, non-error response
        if not is_error:
            self.add_to_main_button.Enable()
        
        self.question_text_ctrl.SetFocus()