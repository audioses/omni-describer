# audio_describer/ui/settings_dialog.py
import wx
import sys
import os
import tempfile
import threading
from audio_describer import config
from audio_describer.models import config_model, voice_model
from audio_describer.utils.logger import app_logger
from audio_describer.ui.accessibility_utils import set_control_accessible_name, speak_message
from audio_describer.utils import sound_player
from .manage_voices_dialog import ManageVoicesDialog
from ..i18n_setup import _
from ..core import tts_engine, piper_voices, gemini_helpers

# --- MODIFICATION: Check for all available TTS engines ---
PYTTSX3_AVAILABLE = tts_engine.PYTTSX3_AVAILABLE
SAPI5_32BIT_HELPER_AVAILABLE = tts_engine.SAPI5_32BIT_HELPER_AVAILABLE
ONECORE_AVAILABLE = tts_engine.WIN_ONECORE_AVAILABLE


# Custom event for when the test voice thread is done
EVT_TEST_VOICE_DONE_ID = wx.NewIdRef()
EVT_TEST_VOICE_DONE = wx.PyEventBinder(EVT_TEST_VOICE_DONE_ID, 0)

class TestVoiceDoneEvent(wx.PyCommandEvent):
    def __init__(self, error=None):
        super().__init__(EVT_TEST_VOICE_DONE_ID, 0)
        self.error = error

# Custom event for when the model-list fetch thread is done
EVT_MODEL_FETCH_DONE_ID = wx.NewIdRef()
EVT_MODEL_FETCH_DONE = wx.PyEventBinder(EVT_MODEL_FETCH_DONE_ID, 0)

class ModelFetchDoneEvent(wx.PyCommandEvent):
    def __init__(self, models=None, error=None):
        super().__init__(EVT_MODEL_FETCH_DONE_ID, 0)
        self.models = models
        self.error = error

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, title=None):
        if title is None:
            title = _("%s Settings") % (config.APP_NAME_FIXED_PREFIX + config.APP_NAME_TRANSLATABLE)
        
        self.YOUTUBE_QUALITY_CHOICES = [_("Best Available"), "1080p", "720p", "480p", "360p", "240p", "144p"]
        self.VERBOSITY_CHOICES = [(config.VERBOSITY_SHORT, _("Short - Concise descriptions")), (config.VERBOSITY_STANDARD, _("Standard - Balanced detail (Default)")), (config.VERBOSITY_DETAILED, _("Detailed - Rich descriptions"))]
        
        self.LANGUAGE_CHOICES = [
            ("", _("Auto-Detect on Startup")),
            ("ar", _("Arabic")),
            ("en", _("English")),
            ("es", _("Spanish")),
            ("fr", _("French")),
            ("it", _("Italian")),
            ("pt", _("Portuguese")),
            ("ru", _("Russian")),
            ("tr", _("Türkçe")),
            ("uk", _("Ukrainian")),
            ("vi", _("Vietnamese")),
        ]

        self.LOGGING_LEVEL_CHOICES = [("INFO", _("Info (Default)")), ("DEBUG", _("Debug")), ("DISABLED", _("Disabled"))]
        self.FRAME_RATE_CHOICES = [
            (0, _("No Change (Original FPS)")),
            (10, _("10 FPS (Good balance, lower cost)")),
            (5, _("5 FPS (Very low cost, may miss fast action)")),
            (2, _("2 FPS (Extreme low cost, for static scenes)")),
            (1, _("1 FPS (Minimal cost, for static content)")),
        ]

        self.GEMINI_MODEL_CHOICES = [
            ("", _("Default (gemini-flash-latest, always the newest Flash)")),
            ("gemini-4-hydrogen", _("Gemini 4 Hydrogen (fast, current Flash tier)")),
            ("gemini-4-neon", _("Gemini 4 Neon (deepest reasoning, slower and pricier)")),
            ("gemini-3.8-flash", _("Gemini 3.8 Flash")),
            ("gemini-3.5-flash-lite", _("Gemini 3.5 Flash Lite (cheapest)")),
        ]
        
        # --- MODIFICATION: Dynamically build TTS choices based on availability ---
        self.TTS_ENGINE_CHOICES = [("openai", _("OpenAI TTS (High Quality)"))]
        if PYTTSX3_AVAILABLE:
            self.TTS_ENGINE_CHOICES.append(("sapi5", _("SAPI5 (64-bit Voices)")))
        if SAPI5_32BIT_HELPER_AVAILABLE:
            self.TTS_ENGINE_CHOICES.append(("sapi5_32bit", _("SAPI5 (32-bit Voices)")))
        if ONECORE_AVAILABLE:
            self.TTS_ENGINE_CHOICES.append(("onecore", _("Windows OneCore (Modern Voices)")))
        # Cloud neural voices: plain HTTPS, always available once a key is entered below.
        self.TTS_ENGINE_CHOICES.append(("azure", _("Microsoft Azure Neural Voices (online, key required)")))
        self.TTS_ENGINE_CHOICES.append(("google", _("Google Cloud Text-to-Speech (online, key required)")))

        self.OPENAI_VOICE_CHOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "ash", "ballad", "sage"]

        super(SettingsDialog, self).__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.settings_changed = False
        self.current_settings = config_model.app_settings.copy() 
        
        self.sapi_voices = [] # This will be populated dynamically

        self.InitUI()
        self.LoadSettingsToUI()
        self.SetSizerAndFit(self.main_sizer)
        self.Layout()
        self.CentreOnParent()

    def InitUI(self):
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)
        
        # --- General Tab ---
        self.general_panel = wx.Panel(self.notebook)
        general_grid_sizer = wx.FlexGridSizer(cols=2, gap=(5,5)); general_grid_sizer.AddGrowableCol(1,1)
        general_grid_sizer.Add(wx.StaticText(self.general_panel, label=_("YouTube Video Download Quality:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.yt_quality_choice = wx.Choice(self.general_panel, choices=self.YOUTUBE_QUALITY_CHOICES)
        general_grid_sizer.Add(self.yt_quality_choice, 1, wx.EXPAND | wx.ALL, 5)
        general_grid_sizer.Add(wx.StaticText(self.general_panel, label=_("Application Language (Requires Restart):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.app_lang_display_choices = [lc[1] for lc in self.LANGUAGE_CHOICES]; self.app_lang_internal_values = [lc[0] for lc in self.LANGUAGE_CHOICES]
        self.app_lang_choice = wx.Choice(self.general_panel, choices=self.app_lang_display_choices)
        general_grid_sizer.Add(self.app_lang_choice, 1, wx.EXPAND | wx.ALL, 5)
        general_grid_sizer.Add(wx.StaticText(self.general_panel, label=_("Logging Level:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.log_level_display_choices = [lc[1] for lc in self.LOGGING_LEVEL_CHOICES]; self.log_level_internal_values = [lc[0] for lc in self.LOGGING_LEVEL_CHOICES]
        self.log_level_choice = wx.Choice(self.general_panel, choices=self.log_level_display_choices)
        general_grid_sizer.Add(self.log_level_choice, 1, wx.EXPAND | wx.ALL, 5)
        self.interrupt_speech_checkbox = wx.CheckBox(self.general_panel, label=_("Allow descriptions to interrupt current speech"))
        general_grid_sizer.Add(self.interrupt_speech_checkbox, 1, wx.EXPAND | wx.ALL, 5)
        self.general_panel.SetSizer(general_grid_sizer)
        self.notebook.AddPage(self.general_panel, _("General"))

        # --- AI Settings Tab ---
        self.ai_panel = wx.Panel(self.notebook)
        ai_grid_sizer = wx.FlexGridSizer(cols=2, gap=(5, 5)); ai_grid_sizer.AddGrowableCol(1, 1)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Gemini API Key:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.api_key_text = wx.TextCtrl(self.ai_panel, style=wx.TE_PASSWORD)
        ai_grid_sizer.Add(self.api_key_text, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("OpenAI API Key (for TTS):")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.openai_api_key_text = wx.TextCtrl(self.ai_panel, style=wx.TE_PASSWORD)
        ai_grid_sizer.Add(self.openai_api_key_text, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticLine(self.ai_panel, style=wx.LI_HORIZONTAL), 0, wx.EXPAND|wx.ALL, 10); ai_grid_sizer.Add(wx.StaticLine(self.ai_panel, style=wx.LI_HORIZONTAL), 0, wx.EXPAND|wx.ALL, 10)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Frame Rate for AI Analysis:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.frame_rate_display_choices = [fc[1] for fc in self.FRAME_RATE_CHOICES]; self.frame_rate_internal_values = [fc[0] for fc in self.FRAME_RATE_CHOICES]
        self.frame_rate_choice = wx.Choice(self.ai_panel, choices=self.frame_rate_display_choices)
        ai_grid_sizer.Add(self.frame_rate_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Description Verbosity:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.verbosity_display_choices = [vc[1] for vc in self.VERBOSITY_CHOICES]; self.verbosity_internal_values = [vc[0] for vc in self.VERBOSITY_CHOICES]
        self.verbosity_choice = wx.Choice(self.ai_panel, choices=self.verbosity_display_choices)
        ai_grid_sizer.Add(self.verbosity_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Gemini Model:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self._models_from_api = False
        self._model_fetch_silent = False
        model_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.model_choice = wx.Choice(self.ai_panel, choices=[])
        model_sizer.Add(self.model_choice, 1, wx.EXPAND|wx.RIGHT, 5)
        self.fetch_models_button = wx.Button(self.ai_panel, label=_("Fetch from API key"))
        model_sizer.Add(self.fetch_models_button, 0, wx.ALIGN_CENTER_VERTICAL)
        ai_grid_sizer.Add(model_sizer, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Temperature:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.temp_text_ctrl = wx.TextCtrl(self.ai_panel, value="0.7")
        ai_grid_sizer.Add(self.temp_text_ctrl, 1, wx.EXPAND|wx.ALL, 5)
        self.send_silenced_video_checkbox = wx.CheckBox(self.ai_panel, label=_("Send Video Only (No Audio) to AI"))
        ai_grid_sizer.Add(self.send_silenced_video_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.disable_safety_checkbox = wx.CheckBox(self.ai_panel, label=_("Disable Safety Filters (Use with caution)"))
        ai_grid_sizer.Add(self.disable_safety_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.enable_chunking_checkbox = wx.CheckBox(self.ai_panel, label=_("Enable Video Chunking for Long Videos"))
        ai_grid_sizer.Add(self.enable_chunking_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.enable_character_glossary_checkbox = wx.CheckBox(self.ai_panel, label=_("Enable Character Glossary Pre-Analysis"))
        ai_grid_sizer.Add(self.enable_character_glossary_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.translate_dialogue_checkbox = wx.CheckBox(self.ai_panel, label=_("Translate dialogue spoken in another language (audio subtitles)"))
        ai_grid_sizer.Add(self.translate_dialogue_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.speech_pauses_checkbox = wx.CheckBox(self.ai_panel, label=_("Find pauses in the dialogue before describing (local speech detection, recommended)"))
        ai_grid_sizer.Add(self.speech_pauses_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.chunk_duration_label = wx.StaticText(self.ai_panel, label=_("Chunk Duration (seconds):"))
        ai_grid_sizer.Add(self.chunk_duration_label, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.chunk_duration_spin = wx.SpinCtrl(self.ai_panel, value="120", min=30, max=2400)
        ai_grid_sizer.Add(self.chunk_duration_spin, 0, wx.EXPAND|wx.ALL, 5)
        self.ai_panel.SetSizer(ai_grid_sizer)
        self.notebook.AddPage(self.ai_panel, _("AI Settings"))

        # --- Audio Output Tab ---
        self.audio_output_panel = wx.Panel(self.notebook)
        audio_sizer = wx.BoxSizer(wx.VERTICAL)
        self.tts_engine_choice = wx.RadioBox(self.audio_output_panel, label=_("Text-to-Speech Engine"), choices=[c[1] for c in self.TTS_ENGINE_CHOICES], majorDimension=1, style=wx.RA_SPECIFY_COLS)
        audio_sizer.Add(self.tts_engine_choice, 0, wx.EXPAND | wx.ALL, 5)
        self.openai_settings_panel = wx.Panel(self.audio_output_panel)
        openai_sizer = wx.StaticBoxSizer(wx.StaticBox(self.openai_settings_panel, label=_("OpenAI TTS Settings")), wx.VERTICAL)
        openai_grid = wx.FlexGridSizer(cols=2, gap=(5,5)); openai_grid.AddGrowableCol(1,1)
        openai_grid.Add(wx.StaticText(self.openai_settings_panel, label=_("Voice Preset:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.openai_voice_choice = wx.Choice(self.openai_settings_panel)
        openai_grid.Add(self.openai_voice_choice, 1, wx.EXPAND|wx.ALL, 5)
        openai_sizer.Add(openai_grid, 0, wx.EXPAND|wx.ALL, 5)
        self.manage_voices_button = wx.Button(self.openai_settings_panel, label=_("Manage Voice Presets..."))
        openai_sizer.Add(self.manage_voices_button, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        self.openai_settings_panel.SetSizer(openai_sizer)
        audio_sizer.Add(self.openai_settings_panel, 0, wx.EXPAND|wx.ALL, 5)
        self.sapi5_settings_panel = wx.Panel(self.audio_output_panel)
        
        # MODIFICATION: Keep a reference to the sizer, not just the panel
        self.sapi_sizer = wx.StaticBoxSizer(wx.StaticBox(self.sapi5_settings_panel, label=_("SAPI5 Settings")), wx.VERTICAL)
        sapi_grid = wx.FlexGridSizer(cols=2, gap=(5,5)); sapi_grid.AddGrowableCol(1,1)
        sapi_grid.Add(wx.StaticText(self.sapi5_settings_panel, label=_("Voice:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sapi_voice_choice = wx.Choice(self.sapi5_settings_panel) # Choices populated dynamically
        sapi_grid.Add(self.sapi_voice_choice, 1, wx.EXPAND|wx.ALL, 5)
        sapi_grid.Add(wx.StaticText(self.sapi5_settings_panel, label=_("Speed:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.sapi_rate_slider = wx.Slider(self.sapi5_settings_panel, value=100, minValue=50, maxValue=200)
        sapi_grid.Add(self.sapi_rate_slider, 1, wx.EXPAND|wx.ALL, 5)
        self.sapi_sizer.Add(sapi_grid, 1, wx.EXPAND | wx.ALL, 5)
        self.sapi5_settings_panel.SetSizer(self.sapi_sizer)
        
        audio_sizer.Add(self.sapi5_settings_panel, 0, wx.EXPAND|wx.ALL, 5)

        # (setting key, label, secret) per cloud engine
        cloud_fields = {
            "azure": (_("Microsoft Azure Neural Voices"), [
                ("user_azure_api_key", _("Speech resource key:"), True),
                ("azure_region", _("Region (for example westeurope):"), False),
                ("azure_voice", _("Voice (for example fr-FR-DeniseNeural):"), False),
                ("azure_voice_female", _("Woman's voice for translated dialogue (optional):"), False),
                ("azure_voice_male", _("Man's voice for translated dialogue (optional):"), False)]),
            "google": (_("Google Cloud Text-to-Speech"), [
                ("user_google_tts_api_key", _("API key:"), True),
                ("google_voice", _("Voice (for example fr-FR-Chirp3-HD-Aoede):"), False),
                ("google_voice_female", _("Woman's voice for translated dialogue (optional):"), False),
                ("google_voice_male", _("Man's voice for translated dialogue (optional):"), False)]),
        }
        self.cloud_panels, self.cloud_ctrls = {}, {}
        for engine, (box_title, fields) in cloud_fields.items():
            panel = wx.Panel(self.audio_output_panel)
            box = wx.StaticBoxSizer(wx.StaticBox(panel, label=box_title), wx.VERTICAL)
            grid = wx.FlexGridSizer(cols=2, gap=(5, 5)); grid.AddGrowableCol(1, 1)
            for key, label, secret in fields:
                grid.Add(wx.StaticText(box.GetStaticBox(), label=label), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
                ctrl = wx.TextCtrl(box.GetStaticBox(), style=wx.TE_PASSWORD if secret else 0)  # named by the label before it
                ctrl.Bind(wx.EVT_TEXT, self.OnSettingChanged)
                grid.Add(ctrl, 1, wx.EXPAND|wx.ALL, 5)
                self.cloud_ctrls[key] = ctrl
            box.Add(grid, 0, wx.EXPAND|wx.ALL, 5)
            panel.SetSizer(box)
            audio_sizer.Add(panel, 0, wx.EXPAND|wx.ALL, 5)
            self.cloud_panels[engine] = panel

        self.gender_voice_checkbox = wx.CheckBox(self.audio_output_panel, label=_("Read translated dialogue with a voice matching the speaker (woman or man)"))
        audio_sizer.Add(self.gender_voice_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        lang = config_model.get_setting("application_language") or "en"
        self.piper_choices, self.piper_buttons = {}, {}
        for gender, label in (("female", _("Woman's voice:")), ("male", _("Man's voice:"))):
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(self.audio_output_panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
            # The voice known to be a woman's / a man's comes first (Piper model cards do not say).
            voices = sorted(piper_voices.voices_for(lang), key=lambda v, g=gender: v[0] != piper_voices.default_voice(lang, g))
            ids = [""] + [v[0] for v in voices]
            names = [_("Installed Windows voice (automatic)")] + [
                _("%(voice)s (local Piper voice, %(size)d MB)") % {"voice": v[0], "size": v[1]} for v in voices]
            choice = wx.Choice(self.audio_output_panel, choices=names)
            choice.voice_ids = ids
            choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
            row.Add(choice, 1, wx.EXPAND|wx.ALL, 5)
            button = wx.Button(self.audio_output_panel, label=_("Download and listen"))
            button.Bind(wx.EVT_BUTTON, lambda e, g=gender: self.OnPiperListen(g))
            row.Add(button, 0, wx.ALL, 5)
            audio_sizer.Add(row, 0, wx.EXPAND)
            self.piper_choices[gender], self.piper_buttons[gender] = choice, button

        test_box = wx.StaticBox(self.audio_output_panel, label=_("Test Voice"))
        test_sizer = wx.StaticBoxSizer(test_box, wx.VERTICAL)
        self.test_text_ctrl = wx.TextCtrl(self.audio_output_panel, value=_("This is a test of the selected voice."))
        test_sizer.Add(self.test_text_ctrl, 0, wx.EXPAND|wx.ALL, 5)
        self.test_voice_button = wx.Button(self.audio_output_panel, label=_("Test"))
        test_sizer.Add(self.test_voice_button, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        audio_sizer.Add(test_sizer, 0, wx.EXPAND|wx.ALL, 5)
        
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Original Audio Ducking (-dB, for MP3):")), 0, wx.ALL, 5)
        self.ducking_spin = wx.SpinCtrl(self.audio_output_panel, value="-15", min=-60, max=0)
        audio_sizer.Add(self.ducking_spin, 0, wx.EXPAND|wx.ALL, 5)
        self.audio_output_panel.SetSizer(audio_sizer)
        self.notebook.AddPage(self.audio_output_panel, _("Audio Output"))

        self.main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)
        button_sizer = wx.StdDialogButtonSizer()
        self.apply_button = wx.Button(self, wx.ID_APPLY); button_sizer.AddButton(self.apply_button)
        self.ok_button = wx.Button(self, wx.ID_OK); self.ok_button.SetDefault(); button_sizer.AddButton(self.ok_button)
        self.cancel_button = wx.Button(self, wx.ID_CANCEL); button_sizer.AddButton(self.cancel_button)
        button_sizer.Realize(); self.main_sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        self.BindAllEvents()
        self.apply_button.Disable()

    def BindAllEvents(self):
        self.Bind(EVT_TEST_VOICE_DONE, self._OnTestVoiceFinished)
        self.Bind(wx.EVT_BUTTON, self.OnTestVoice, self.test_voice_button)
        self.Bind(wx.EVT_BUTTON, self.OnOK, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self.OnApply, id=wx.ID_APPLY)
        self.Bind(wx.EVT_BUTTON, self.OnCancel, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self.OnCancel)
        self.yt_quality_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.app_lang_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.log_level_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.interrupt_speech_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.api_key_text.Bind(wx.EVT_TEXT, self.OnSettingChanged)
        self.openai_api_key_text.Bind(wx.EVT_TEXT, self.OnSettingChanged)
        self.frame_rate_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.verbosity_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.model_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.Bind(EVT_MODEL_FETCH_DONE, self._OnModelFetchFinished)
        self.Bind(wx.EVT_BUTTON, self.OnFetchModels, self.fetch_models_button)
        self.temp_text_ctrl.Bind(wx.EVT_TEXT, self.OnSettingChanged)
        self.temp_text_ctrl.Bind(wx.EVT_KILL_FOCUS, self.OnTemperatureKillFocus)
        self.send_silenced_video_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.disable_safety_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.enable_chunking_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.chunk_duration_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.enable_character_glossary_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.translate_dialogue_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.speech_pauses_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.gender_voice_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.tts_engine_choice.Bind(wx.EVT_RADIOBOX, self.OnTtsEngineChanged)
        self.openai_voice_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.manage_voices_button.Bind(wx.EVT_BUTTON, self.OnManageVoices)
        self.sapi_voice_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.sapi_rate_slider.Bind(wx.EVT_SLIDER, self.OnSettingChanged)
        self.ducking_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)

    def OnTestVoice(self, event):
        text = self.test_text_ctrl.GetValue()
        if not text:
            wx.MessageBox(_("Please enter some text to test."), _("Input Required"), wx.OK | wx.ICON_WARNING, self)
            return

        engine_choice = self.TTS_ENGINE_CHOICES[self.tts_engine_choice.GetSelection()][0]
        
        settings = {
            "openai_api_key": self.openai_api_key_text.GetValue().strip(),
            "openai_voice_preset": self.openai_voice_choice.GetStringSelection(),
            "openai_tts_model": config_model.get_setting("openai_tts_model"),
            "sapi5_voice_id": self.sapi_voices[self.sapi_voice_choice.GetSelection()]['id'] if self.sapi_voices and self.sapi_voice_choice.GetSelection() != wx.NOT_FOUND else None,
            "sapi5_rate_percent": self.sapi_rate_slider.GetValue(),
            "onecore_voice_id": self.sapi_voices[self.sapi_voice_choice.GetSelection()]['id'] if self.sapi_voices and self.sapi_voice_choice.GetSelection() != wx.NOT_FOUND else None,
            "onecore_rate_percent": self.sapi_rate_slider.GetValue(),
            **{key: ctrl.GetValue().strip() for key, ctrl in self.cloud_ctrls.items()},
        }
        
        self.test_voice_button.Disable()
        wx.BeginBusyCursor()
        thread = threading.Thread(target=self._TestVoiceThread, args=(engine_choice, text, settings))
        thread.daemon = True
        thread.start()

    def _TestVoiceThread(self, engine_choice, text, settings):
        error = None
        try:
            tts_engine.test_voice(engine_choice, text, settings)
        except Exception as e:
            error = e
        finally:
            wx.PostEvent(self, TestVoiceDoneEvent(error=error))

    def _OnTestVoiceFinished(self, event):
        wx.EndBusyCursor()
        self.test_voice_button.Enable()
        if event.error:
            wx.MessageBox(_("Error testing voice: %s") % event.error, _("Test Failed"), wx.OK | wx.ICON_ERROR, self)
            
    def _RebuildModelChoices(self, model_pairs):
        pairs = list(model_pairs) if model_pairs else list(self.GEMINI_MODEL_CHOICES[1:])
        self.model_internal_values = [""] + [pair[0] for pair in pairs]
        self.model_display_choices = [self.GEMINI_MODEL_CHOICES[0][1]] + [pair[1] for pair in pairs]
        self.model_choice.SetItems(self.model_display_choices)

    def _ApplyModelSelection(self, model_id):
        if model_id in self.model_internal_values:
            self.model_choice.SetSelection(self.model_internal_values.index(model_id))
        else:
            self.model_choice.SetSelection(0)

    def OnFetchModels(self, event, silent=False):
        entered_key = self.api_key_text.GetValue().strip()
        self._model_fetch_silent = silent
        self.fetch_models_button.Disable()
        thread = threading.Thread(target=self._ModelFetchThread, args=(entered_key,))
        thread.daemon = True
        thread.start()

    def _ModelFetchThread(self, api_key):
        models = None
        error = None
        try:
            models = gemini_helpers.fetch_available_models(api_key or None)
        except Exception as e:
            error = e
        finally:
            wx.PostEvent(self, ModelFetchDoneEvent(models=models, error=error))

    def _OnModelFetchFinished(self, event):
        self.fetch_models_button.Enable()
        if event.error:
            app_logger.error("Model list fetch failed: %s", event.error)
            if not self._model_fetch_silent:
                wx.MessageBox(_("Error fetching models: %s") % event.error, _("Fetch Failed"), wx.OK | wx.ICON_ERROR, self)
            return
        if event.models:
            selection = self.model_choice.GetSelection()
            current_value = self.model_internal_values[selection] if selection != wx.NOT_FOUND else ""
            self._models_from_api = True
            self._RebuildModelChoices(event.models)
            self._ApplyModelSelection(current_value)
            self.settings_changed = True
            self.apply_button.Enable()

    def OnTtsEngineChanged(self, event):
        self.UpdateTtsEnginePanels()
        self.OnSettingChanged(event)

    def UpdateTtsEnginePanels(self):
        selected_engine = self.TTS_ENGINE_CHOICES[self.tts_engine_choice.GetSelection()][0]
        
        is_openai = (selected_engine == "openai")
        is_sapi = selected_engine.startswith("sapi5") or selected_engine == "onecore"
        
        self.openai_settings_panel.Show(is_openai)
        self.sapi5_settings_panel.Show(is_sapi)
        for engine, panel in self.cloud_panels.items():
            panel.Show(selected_engine == engine)
        # OneCore goes up to 6x; existing OneCore speeds above 200% were kept by the rate migration.
        self.sapi_rate_slider.SetMax(config_model.ONECORE_RATE_MAX_PERCENT if selected_engine == "onecore" else 200)
        # SetMax clamps the value: show this engine's saved rate, or a OneCore 300% would be saved as 200%.
        rate_key = "onecore_voice_rate_percent" if selected_engine == "onecore" else "sapi5_voice_rate_percent"
        self.sapi_rate_slider.SetValue(self.current_settings.get(rate_key) or 100)

        if is_sapi:
            # MODIFICATION: Use the sizer reference to get the static box
            box_label = _("SAPI5 (64-bit) Settings")
            if selected_engine == "sapi5_32bit":
                self.sapi_voices = tts_engine.get_available_sapi5_voices_32bit()
                box_label = _("SAPI5 (32-bit) Settings")
            elif selected_engine == "onecore":
                self.sapi_voices = tts_engine.get_available_onecore_voices()
                box_label = _("Windows OneCore Settings")
            else: # sapi5 (64-bit)
                self.sapi_voices = tts_engine.get_available_sapi5_voices()
            self.sapi_sizer.GetStaticBox().SetLabel(box_label)
            
            self.sapi_voice_choice.Clear()
            if self.sapi_voices:
                self.sapi_voice_choice.Enable()
                for voice in self.sapi_voices:
                    self.sapi_voice_choice.Append(voice['name'])
            else:
                self.sapi_voice_choice.Append(_("(No voices found)"))
                self.sapi_voice_choice.Disable()
            self.sapi_voice_choice.SetSelection(0)

        self.audio_output_panel.Layout()
        
    def OnManageVoices(self, event):
        with ManageVoicesDialog(self) as dlg:
            dlg.ShowModal()
        self.PopulateOpenAiVoices()
        self.OnSettingChanged(event)

    def PopulateOpenAiVoices(self):
        self.openai_voice_choice.Clear()
        voices = voice_model.get_voices()
        for voice in voices:
            self.openai_voice_choice.Append(voice["name"])
        
        current_preset = self.current_settings.get("openai_tts_voice", "")
        if current_preset and self.openai_voice_choice.FindString(current_preset) != wx.NOT_FOUND:
            self.openai_voice_choice.SetStringSelection(current_preset)
        elif self.openai_voice_choice.GetCount() > 0:
            self.openai_voice_choice.SetSelection(0)

    def LoadSettingsToUI(self):
        self.current_settings = config_model.app_settings.copy()
        
        self.yt_quality_choice.SetStringSelection(self.current_settings.get("youtube_download_quality", "144p"))
        
        saved_lang = self.current_settings.get("application_language", "")
        try:
            self.app_lang_choice.SetSelection(self.app_lang_internal_values.index(saved_lang))
        except ValueError:
            self.app_lang_choice.SetSelection(0)
            
        self.log_level_choice.SetSelection(self.log_level_internal_values.index(self.current_settings.get("logging_level", "INFO")))
        self.interrupt_speech_checkbox.SetValue(self.current_settings.get("player_allow_speech_interruption", True))
        
        self.api_key_text.SetValue(self.current_settings.get("user_gemini_api_key", ""))
        self.openai_api_key_text.SetValue(self.current_settings.get("user_openai_api_key", ""))
        self.frame_rate_choice.SetSelection(self.frame_rate_internal_values.index(self.current_settings.get("frame_rate_for_ai", 0)))
        self.verbosity_choice.SetSelection(self.verbosity_internal_values.index(self.current_settings.get("gemini_description_verbosity", config.DEFAULT_VERBOSITY)))
        cached_models = self.current_settings.get("gemini_available_models") or []
        self._models_from_api = bool(cached_models)
        self._RebuildModelChoices([tuple(pair) for pair in cached_models])
        self._ApplyModelSelection(self.current_settings.get("gemini_model_override", ""))
        if self.current_settings.get("user_gemini_api_key", "").strip():
            self.OnFetchModels(None, silent=True)
        self.temp_text_ctrl.SetValue(str(self.current_settings.get("gemini_temperature", 0.7)))
        self.send_silenced_video_checkbox.SetValue(self.current_settings.get("send_silenced_video_to_ai", True))
        self.disable_safety_checkbox.SetValue(self.current_settings.get("gemini_disable_safety_block_none", False))
        
        is_chunking = self.current_settings.get("enable_video_chunking", False)
        self.enable_chunking_checkbox.SetValue(is_chunking)
        self.chunk_duration_spin.SetValue(self.current_settings.get("video_chunk_duration_seconds", 120))
        self.chunk_duration_label.Enable(is_chunking)
        self.chunk_duration_spin.Enable(is_chunking)
        self.enable_character_glossary_checkbox.SetValue(self.current_settings.get("enable_character_glossary", False))
        self.translate_dialogue_checkbox.SetValue(bool(self.current_settings.get("translate_foreign_dialogue", False)))
        self.speech_pauses_checkbox.SetValue(bool(self.current_settings.get("detect_speech_pauses", False)))
        
        tts_engine_val = self.current_settings.get("tts_engine", "sapi5")
        try:
            self.tts_engine_choice.SetSelection([c[0] for c in self.TTS_ENGINE_CHOICES].index(tts_engine_val))
        except ValueError:
            self.tts_engine_choice.SetSelection(0)
        
        self.UpdateTtsEnginePanels()
        self.PopulateOpenAiVoices()
        
        # Load voice settings based on the selected TTS engine
        selected_engine = self.current_settings.get("tts_engine", "sapi5")
        if selected_engine == "onecore":
            saved_voice_id = self.current_settings.get("onecore_voice_id")
        else:
            saved_voice_id = self.current_settings.get("sapi5_voice_id")
            
        for i, voice in enumerate(self.sapi_voices):
            if voice['id'] == saved_voice_id:
                self.sapi_voice_choice.SetSelection(i)
                break
        
        # Load rate setting
        if selected_engine == "onecore":
            self.sapi_rate_slider.SetValue(self.current_settings.get("onecore_voice_rate_percent", 100))
        else:
            self.sapi_rate_slider.SetValue(self.current_settings.get("sapi5_voice_rate_percent", 100))
        self.ducking_spin.SetValue(self.current_settings.get("mp3_audio_ducking_level_db", -15))
        for key, ctrl in self.cloud_ctrls.items():
            ctrl.ChangeValue(str(self.current_settings.get(key) or ""))
        self.gender_voice_checkbox.SetValue(bool(self.current_settings.get("dialogue_voice_by_gender", True)))
        for gender, choice in self.piper_choices.items():
            voice_id = self.current_settings.get(f"piper_voice_{gender}") or ""
            if voice_id not in choice.voice_ids:  # chosen for another language
                choice.Append(voice_id)
                choice.voice_ids.append(voice_id)
            choice.SetSelection(choice.voice_ids.index(voice_id))

        self.settings_changed = False
        self.apply_button.Disable()

    def ApplySettings(self):
        self.current_settings["youtube_download_quality"] = self.yt_quality_choice.GetStringSelection()
        self.current_settings["application_language"] = self.app_lang_internal_values[self.app_lang_choice.GetSelection()]
        self.current_settings["logging_level"] = self.log_level_internal_values[self.log_level_choice.GetSelection()]
        self.current_settings["player_allow_speech_interruption"] = self.interrupt_speech_checkbox.IsChecked()
        
        self.current_settings["user_gemini_api_key"] = self.api_key_text.GetValue().strip()
        self.current_settings["user_openai_api_key"] = self.openai_api_key_text.GetValue().strip()
        self.current_settings["frame_rate_for_ai"] = self.frame_rate_internal_values[self.frame_rate_choice.GetSelection()]
        self.current_settings["gemini_description_verbosity"] = self.verbosity_internal_values[self.verbosity_choice.GetSelection()]
        model_selection = self.model_choice.GetSelection()
        self.current_settings["gemini_model_override"] = self.model_internal_values[model_selection] if model_selection != wx.NOT_FOUND else ""
        if self._models_from_api:
            self.current_settings["gemini_available_models"] = [[pair_id, pair_label] for pair_id, pair_label in zip(self.model_internal_values[1:], self.model_display_choices[1:])]
        try: self.current_settings["gemini_temperature"] = float(self.temp_text_ctrl.GetValue().strip())
        except ValueError: self.current_settings["gemini_temperature"] = config_model.DEFAULT_SETTINGS["gemini_temperature"]
        self.current_settings["send_silenced_video_to_ai"] = self.send_silenced_video_checkbox.IsChecked()
        self.current_settings["gemini_disable_safety_block_none"] = self.disable_safety_checkbox.IsChecked()
        self.current_settings["enable_video_chunking"] = self.enable_chunking_checkbox.IsChecked()
        self.current_settings["video_chunk_duration_seconds"] = self.chunk_duration_spin.GetValue()
        self.current_settings["enable_character_glossary"] = self.enable_character_glossary_checkbox.IsChecked()
        self.current_settings["translate_foreign_dialogue"] = self.translate_dialogue_checkbox.IsChecked()
        self.current_settings["detect_speech_pauses"] = self.speech_pauses_checkbox.IsChecked()
        
        self.current_settings["tts_engine"] = self.TTS_ENGINE_CHOICES[self.tts_engine_choice.GetSelection()][0]
        self.current_settings["openai_tts_voice"] = self.openai_voice_choice.GetStringSelection()
        
        # Save voice settings based on the selected TTS engine
        selected_engine = self.current_settings["tts_engine"]
        voice_id = None
        if self.sapi_voices and self.sapi_voice_choice.GetSelection() != wx.NOT_FOUND:
            voice_id = self.sapi_voices[self.sapi_voice_choice.GetSelection()]['id']
        
        # Save to appropriate settings based on engine
        if selected_engine == "onecore":
            self.current_settings["onecore_voice_id"] = voice_id
            self.current_settings["onecore_voice_rate_percent"] = self.sapi_rate_slider.GetValue()
            # Also save to sapi5 for backward compatibility
            self.current_settings["sapi5_voice_id"] = voice_id
            self.current_settings["sapi5_voice_rate_percent"] = self.sapi_rate_slider.GetValue()
        else:
            # For other engines (sapi5, sapi5_32bit), save to sapi5 settings
            self.current_settings["sapi5_voice_id"] = voice_id
            self.current_settings["sapi5_voice_rate_percent"] = self.sapi_rate_slider.GetValue()
            # Clear onecore settings when not using onecore
            self.current_settings["onecore_voice_id"] = None
            self.current_settings["onecore_voice_rate_percent"] = 100

        self.current_settings["mp3_audio_ducking_level_db"] = self.ducking_spin.GetValue()
        for key, ctrl in self.cloud_ctrls.items():
            self.current_settings[key] = ctrl.GetValue().strip()
        self.current_settings["dialogue_voice_by_gender"] = self.gender_voice_checkbox.IsChecked()
        for gender, choice in self.piper_choices.items():
            self.current_settings[f"piper_voice_{gender}"] = choice.voice_ids[max(0, choice.GetSelection())]
        
        config_model.app_settings.update(self.current_settings)
        if config_model.save_settings(config_model.app_settings):
            self.settings_changed = False
            self.apply_button.Disable()
            from audio_describer.utils import logger
            logger.update_log_level()
            return True
        else:
            wx.MessageBox(_("Could not save settings."), _("Error"), wx.OK | wx.ICON_ERROR, self)
            return False

    def OnSettingChanged(self, event):
        is_chunking = self.enable_chunking_checkbox.IsChecked()
        self.chunk_duration_label.Enable(is_chunking)
        self.chunk_duration_spin.Enable(is_chunking)
        self.ai_panel.Layout()
        
        self.settings_changed = True
        self.apply_button.Enable()
        event.Skip()

    def OnApply(self, event):
        if self.settings_changed: self.ApplySettings()
        event.Skip() 

    def OnOK(self, event):
        if self.settings_changed:
            if not self.ApplySettings(): return
        self.EndModal(wx.ID_OK)

    def OnCancel(self, event):
        self.EndModal(wx.ID_CANCEL)
        
    def OnPiperListen(self, gender):
        """Download the chosen Piper voice once (about 20 MB, checksum verified), then play a sample."""
        choice = self.piper_choices[gender]
        voice_id = choice.voice_ids[choice.GetSelection()] if choice.GetSelection() >= 0 else ""
        if not voice_id:
            speak_message(_("Choose a Piper voice first; the automatic choice uses the voices installed in Windows."), interrupt=True)
            return
        if not piper_voices.available():
            speak_message(_("Local Piper voices are not available in this version."), interrupt=True)
            return
        button = self.piper_buttons[gender]
        button.Disable()
        speak_message(_("Preparing the voice %s...") % voice_id, interrupt=True)
        path = os.path.join(tempfile.gettempdir(), "omni_describer_piper_test.wav")

        def work():
            try:
                piper_voices.download(voice_id)
                ok, error = piper_voices.synthesize(voice_id, _("This is how this voice sounds."), path), None
            except Exception as e:
                ok, error = False, e
            wx.CallAfter(done, ok, error)

        def done(ok, error):
            if not self:
                return
            button.Enable()
            if ok:
                sound_player.play(path, is_full_path=True)
            else:
                speak_message(_("The voice could not be prepared: %s") % (error or _("no sound produced")), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

    def OnTemperatureKillFocus(self, event):
        pass