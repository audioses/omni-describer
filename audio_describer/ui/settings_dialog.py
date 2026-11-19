# audio_describer/ui/settings_dialog.py
import wx
import os
import sys
import tempfile
import threading
from audio_describer import config
from audio_describer.models import config_model, voice_model
from audio_describer.utils.logger import app_logger
from audio_describer.ui.accessibility_utils import set_control_accessible_name
from .manage_voices_dialog import ManageVoicesDialog
from ..i18n_setup import _
from ..core import tts_engine, timing, piper_voices
from ..utils import sound_player
from ..core import gemini_helpers as gemini
from .accessibility_utils import speak_message

# --- MODIFICATION: Check for all available TTS engines ---
PYTTSX3_AVAILABLE = tts_engine.PYTTSX3_AVAILABLE
SAPI5_32BIT_HELPER_AVAILABLE = tts_engine.SAPI5_32BIT_HELPER_AVAILABLE
ONECORE_AVAILABLE = tts_engine.WIN_ONECORE_AVAILABLE
OPENAI_AVAILABLE = tts_engine.OPENAI_AVAILABLE


# Custom event for when the test voice thread is done
EVT_TEST_VOICE_DONE_ID = wx.NewIdRef()
EVT_TEST_VOICE_DONE = wx.PyEventBinder(EVT_TEST_VOICE_DONE_ID, 0)


def _safe_index(values, value):
    """Position of a stored setting in a choice list; 0 for a value from another app version."""
    return values.index(value) if value in values else 0


class TestVoiceDoneEvent(wx.PyCommandEvent):
    def __init__(self, error=None):
        super().__init__(EVT_TEST_VOICE_DONE_ID, 0)
        self.error = error

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, title=None):
        if title is None:
            title = _("%s Settings") % (config.APP_NAME_FIXED_PREFIX + config.APP_NAME_TRANSLATABLE)
        
        self.YOUTUBE_QUALITY_CHOICES = [_("Best Available"), "1080p", "720p", "480p", "360p", "240p", "144p"]
        self.VERBOSITY_CHOICES = [(config.VERBOSITY_SHORT, _("Essential descriptions only")), (config.VERBOSITY_STANDARD, _("Standard (Default)")), (config.VERBOSITY_DETAILED, _("All descriptions, with details"))]
        
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

        self.DESCRIPTION_LANGUAGE_CHOICES = [("", _("Same as interface"))] + [
            (code, _(name)) for code, name in sorted(timing.LANGUAGE_NAMES.items(), key=lambda kv: kv[1])]
        self.THINKING_LEVEL_CHOICES = [("low", _("Low (faster, cheaper)")), ("medium", _("Medium (Default)")),
                                       ("high", _("High (slower, for complex films)"))]
        # Gemini mediaResolution: tokens per video frame (Google advises low/medium for video, high to read small text).
        self.MEDIA_RESOLUTION_CHOICES = [("", _("Automatic (Default)")), ("low", _("Low (cheaper, for long videos)")),
                                         ("medium", _("Medium")), ("high", _("High (reads small on-screen text, costs more)"))]

        self.STYLE_CHOICES = [("objective", _("Objective (Default)")), ("subjective", _("Subjective, evocative"))]
        self.EMPHASIS_CHOICES = [("general", _("General (Default)")), ("characters", _("Characters")),
                                 ("actions", _("Actions and instructions")), ("environment", _("Settings and atmosphere")),
                                 ("text", _("On-screen text"))]

        self.EXPLICITATION_CHOICES = [("describe", _("Describe only what is visible")),
                                      ("name", _("Describe and name (Default)")),
                                      ("explain", _("Describe, name and briefly explain"))]

        self.LOGGING_LEVEL_CHOICES = [("INFO", _("Info (Default)")), ("DEBUG", _("Debug")), ("DISABLED", _("Disabled"))]
        self.FRAME_RATE_CHOICES = [
            (0, _("No Change (Original FPS)")),
            (10, _("10 FPS (Good balance, lower cost)")),
            (5, _("5 FPS (Very low cost, may miss fast action)")),
            (2, _("2 FPS (Extreme low cost, for static scenes)")),
            (1, _("1 FPS (Minimal cost, for static content)")),
        ]
        
        # --- MODIFICATION: Dynamically build TTS choices based on availability ---
        self.TTS_ENGINE_CHOICES = []
        if OPENAI_AVAILABLE:
            self.TTS_ENGINE_CHOICES.append(("openai", _("OpenAI TTS (High Quality)")))
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
        set_control_accessible_name(self.api_key_text, _("Gemini API Key"))
        key_row = wx.BoxSizer(wx.HORIZONTAL)
        key_row.Add(self.api_key_text, 1, wx.EXPAND|wx.RIGHT, 5)
        self.test_key_button = wx.Button(self.ai_panel, label=_("&Test key"))
        key_row.Add(self.test_key_button, 0)
        ai_grid_sizer.Add(key_row, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("OpenAI API Key (for TTS):")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.openai_api_key_text = wx.TextCtrl(self.ai_panel, style=wx.TE_PASSWORD)
        ai_grid_sizer.Add(self.openai_api_key_text, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticLine(self.ai_panel, style=wx.LI_HORIZONTAL), 0, wx.EXPAND|wx.ALL, 10); ai_grid_sizer.Add(wx.StaticLine(self.ai_panel, style=wx.LI_HORIZONTAL), 0, wx.EXPAND|wx.ALL, 10)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Frame Rate for AI Analysis:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.frame_rate_display_choices = [fc[1] for fc in self.FRAME_RATE_CHOICES]; self.frame_rate_internal_values = [fc[0] for fc in self.FRAME_RATE_CHOICES]
        self.frame_rate_choice = wx.Choice(self.ai_panel, choices=self.frame_rate_display_choices)
        ai_grid_sizer.Add(self.frame_rate_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Description density when the player opens (D changes it):")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.verbosity_display_choices = [vc[1] for vc in self.VERBOSITY_CHOICES]; self.verbosity_internal_values = [vc[0] for vc in self.VERBOSITY_CHOICES]
        self.verbosity_choice = wx.Choice(self.ai_panel, choices=self.verbosity_display_choices)
        ai_grid_sizer.Add(self.verbosity_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Description Language:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.desc_lang_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.DESCRIPTION_LANGUAGE_CHOICES])
        ai_grid_sizer.Add(self.desc_lang_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Description Style:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.style_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.STYLE_CHOICES])
        ai_grid_sizer.Add(self.style_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Focus On:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.emphasis_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.EMPHASIS_CHOICES])
        ai_grid_sizer.Add(self.emphasis_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Maximum Words per Description (0 = fit the pause):")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.max_words_spin = wx.SpinCtrl(self.ai_panel, value="0", min=0, max=150)
        ai_grid_sizer.Add(self.max_words_spin, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Explicitation:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.explicitation_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.EXPLICITATION_CHOICES])
        ai_grid_sizer.Add(self.explicitation_choice, 1, wx.EXPAND|wx.ALL, 5)
        self.translate_dialogue_checkbox = wx.CheckBox(self.ai_panel, label=_("Translate dialogue spoken in another language (audio subtitles)"))
        ai_grid_sizer.Add(self.translate_dialogue_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.AddSpacer(0)
        self.quality_check_checkbox = wx.CheckBox(self.ai_panel, label=_("Check the descriptions with a second AI pass (marks uncertain ones)"))
        ai_grid_sizer.Add(self.quality_check_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.AddSpacer(0)
        self.colors_checkbox = wx.CheckBox(self.ai_panel, label=_("Mention colours"))
        ai_grid_sizer.Add(self.colors_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.AddSpacer(0)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Gemini Model (empty = default %s):") % config.GEMINI_MODEL_NAME), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        model_row = wx.BoxSizer(wx.HORIZONTAL)
        self.model_override_text = wx.ComboBox(self.ai_panel, choices=config.KNOWN_GEMINI_MODELS, style=wx.CB_READONLY)
        set_control_accessible_name(self.model_override_text, _("Gemini Model"))
        model_row.Add(self.model_override_text, 1, wx.EXPAND|wx.RIGHT, 5)
        self.refresh_models_button = wx.Button(self.ai_panel, label=_("&Refresh model list"))
        model_row.Add(self.refresh_models_button, 0)
        ai_grid_sizer.Add(model_row, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Thinking Level (Gemini 3 and later):")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.thinking_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.THINKING_LEVEL_CHOICES])
        ai_grid_sizer.Add(self.thinking_choice, 1, wx.EXPAND|wx.ALL, 5)
        ai_grid_sizer.Add(wx.StaticText(self.ai_panel, label=_("Picture detail sent to the AI:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.media_resolution_choice = wx.Choice(self.ai_panel, choices=[c[1] for c in self.MEDIA_RESOLUTION_CHOICES])
        ai_grid_sizer.Add(self.media_resolution_choice, 1, wx.EXPAND|wx.ALL, 5)
        self.send_silenced_video_checkbox = wx.CheckBox(self.ai_panel, label=_("Send Video Only (No Audio) to AI - not recommended: the AI can no longer avoid dialogue"))
        ai_grid_sizer.Add(self.send_silenced_video_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.disable_safety_checkbox = wx.CheckBox(self.ai_panel, label=_("Disable Safety Filters (Use with caution)"))
        ai_grid_sizer.Add(self.disable_safety_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.enable_chunking_checkbox = wx.CheckBox(self.ai_panel, label=_("Enable Video Chunking for Long Videos"))
        ai_grid_sizer.Add(self.enable_chunking_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.silence_detection_checkbox = wx.CheckBox(self.ai_panel, label=_("Detect pauses in the soundtrack before describing (recommended)"))
        ai_grid_sizer.Add(self.silence_detection_checkbox, 0, wx.EXPAND|wx.ALL, 5)
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
        openai_grid.Add(wx.StaticText(openai_sizer.GetStaticBox(), label=_("Voice Preset:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.openai_voice_choice = wx.Choice(openai_sizer.GetStaticBox())
        openai_grid.Add(self.openai_voice_choice, 1, wx.EXPAND|wx.ALL, 5)
        openai_sizer.Add(openai_grid, 0, wx.EXPAND|wx.ALL, 5)
        self.manage_voices_button = wx.Button(openai_sizer.GetStaticBox(), label=_("Manage Voice Presets..."))
        openai_sizer.Add(self.manage_voices_button, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        self.openai_settings_panel.SetSizer(openai_sizer)
        audio_sizer.Add(self.openai_settings_panel, 0, wx.EXPAND|wx.ALL, 5)
        self.sapi5_settings_panel = wx.Panel(self.audio_output_panel)
        
        # MODIFICATION: Keep a reference to the sizer, not just the panel
        self.sapi_sizer = wx.StaticBoxSizer(wx.StaticBox(self.sapi5_settings_panel, label=_("SAPI5 Settings")), wx.VERTICAL)
        sapi_grid = wx.FlexGridSizer(cols=2, gap=(5,5)); sapi_grid.AddGrowableCol(1,1)
        sapi_grid.Add(wx.StaticText(self.sapi_sizer.GetStaticBox(), label=_("Voice:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sapi_voice_choice = wx.Choice(self.sapi_sizer.GetStaticBox()) # Choices populated dynamically
        sapi_grid.Add(self.sapi_voice_choice, 1, wx.EXPAND|wx.ALL, 5)
        sapi_grid.Add(wx.StaticText(self.sapi_sizer.GetStaticBox(), label=_("Speed:")), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
        self.sapi_rate_slider = wx.Slider(self.sapi_sizer.GetStaticBox(), value=100, minValue=50, maxValue=200)
        sapi_grid.Add(self.sapi_rate_slider, 1, wx.EXPAND|wx.ALL, 5)
        self.sapi_sizer.Add(sapi_grid, 1, wx.EXPAND | wx.ALL, 5)
        self.sapi5_settings_panel.SetSizer(self.sapi_sizer)
        
        audio_sizer.Add(self.sapi5_settings_panel, 0, wx.EXPAND|wx.ALL, 5)

        # (setting key, label, secret) per cloud engine; *_female / *_male read translated dialogue.
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
        for engine, (title, fields) in cloud_fields.items():
            panel = wx.Panel(self.audio_output_panel)
            box = wx.StaticBoxSizer(wx.StaticBox(panel, label=title), wx.VERTICAL)
            grid = wx.FlexGridSizer(cols=2, gap=(5, 5)); grid.AddGrowableCol(1, 1)
            for key, label, secret in fields:
                grid.Add(wx.StaticText(box.GetStaticBox(), label=label), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
                ctrl = wx.TextCtrl(box.GetStaticBox(), style=wx.TE_PASSWORD if secret else 0)
                set_control_accessible_name(ctrl, label.rstrip(":"))
                ctrl.Bind(wx.EVT_TEXT, self.OnSettingChanged)
                grid.Add(ctrl, 1, wx.EXPAND|wx.ALL, 5)
                self.cloud_ctrls[key] = ctrl
            box.Add(grid, 0, wx.EXPAND|wx.ALL, 5)
            panel.SetSizer(box)
            audio_sizer.Add(panel, 0, wx.EXPAND|wx.ALL, 5)
            self.cloud_panels[engine] = panel
        
        test_box = wx.StaticBox(self.audio_output_panel, label=_("Test Voice"))
        test_sizer = wx.StaticBoxSizer(test_box, wx.VERTICAL)
        self.test_text_ctrl = wx.TextCtrl(test_box, value=_("This is a test of the selected voice."))
        test_sizer.Add(self.test_text_ctrl, 0, wx.EXPAND|wx.ALL, 5)
        self.test_voice_button = wx.Button(test_box, label=_("Test"))
        test_sizer.Add(self.test_voice_button, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        audio_sizer.Add(test_sizer, 0, wx.EXPAND|wx.ALL, 5)
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Pronunciation dictionary, one rule per line: word = how to say it (app voice and exports):")), 0, wx.ALL, 5)
        self.pronunciation_text = wx.TextCtrl(self.audio_output_panel, style=wx.TE_MULTILINE, size=(-1, 70))
        set_control_accessible_name(self.pronunciation_text, _("Pronunciation dictionary"))
        audio_sizer.Add(self.pronunciation_text, 0, wx.EXPAND|wx.ALL, 5)
        self.AD_MODE_CHOICES = [("inline", _("In the pauses, the video keeps playing")),
                                ("extended", _("Extended: pause the video when a description does not fit")),
                                ("on_demand", _("On demand: Enter describes the current moment"))]
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Audio description mode in the player (M changes it):")), 0, wx.ALL, 5)
        self.ad_mode_choice = wx.Choice(self.audio_output_panel, choices=[c[1] for c in self.AD_MODE_CHOICES])
        set_control_accessible_name(self.ad_mode_choice, _("Audio description mode in the player"))
        audio_sizer.Add(self.ad_mode_choice, 0, wx.EXPAND|wx.ALL, 5)

        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Original audio lowered during descriptions (-dB, player and export; 6 to 12 recommended):")), 0, wx.ALL, 5)
        self.ducking_spin = wx.SpinCtrl(self.audio_output_panel, value="-9", min=-40, max=0)
        audio_sizer.Add(self.ducking_spin, 0, wx.EXPAND|wx.ALL, 5)
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Audio description voice level in exports (dB, 0 = unchanged):")), 0, wx.ALL, 5)
        self.voice_gain_spin = wx.SpinCtrl(self.audio_output_panel, value="0", min=-20, max=20)
        audio_sizer.Add(self.voice_gain_spin, 0, wx.EXPAND|wx.ALL, 5)
        self.second_voice_checkbox = wx.CheckBox(self.audio_output_panel, label=_("In exports, read on-screen text in a second, lower voice"))
        audio_sizer.Add(self.second_voice_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        self.gender_voice_checkbox = wx.CheckBox(self.audio_output_panel, label=_("Read translated dialogue with a voice matching the speaker (woman or man)"))
        audio_sizer.Add(self.gender_voice_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        lang = (config_model.get_setting("description_language") or config_model.ui_language() or "en")
        self.piper_choices, self.piper_buttons = {}, {}
        for gender, label in (("female", _("Woman's voice:")), ("male", _("Man's voice:"))):
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(self.audio_output_panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5)
            # The voice known to be a woman's / a man's comes first (Piper model cards do not say).
            voices = sorted(piper_voices.voices_for(lang), key=lambda v: v[0] != piper_voices.default_voice(lang, gender))
            ids = [""] + [v[0] for v in voices]
            names = [_("Installed Windows voice (automatic)")] + [
                _("%(voice)s (local Piper voice, %(size)d MB)") % {"voice": v[0], "size": v[1]} for v in voices]
            choice = wx.Choice(self.audio_output_panel, choices=names)
            choice.voice_ids = ids
            set_control_accessible_name(choice, label.rstrip(":"))
            row.Add(choice, 1, wx.EXPAND|wx.ALL, 5)
            button = wx.Button(self.audio_output_panel, label=_("Download and listen"))
            button.Bind(wx.EVT_BUTTON, lambda e, g=gender: self.OnPiperListen(g))
            row.Add(button, 0, wx.ALL, 5)
            audio_sizer.Add(row, 0, wx.EXPAND)
            self.piper_choices[gender], self.piper_buttons[gender] = choice, button
        self.OUTPUT_MODE_CHOICES = [("screen_reader", _("Screen reader (speech and braille)")),
                                    ("app_voice", _("App voice above, prepared in advance (exact timing)")),
                                    ("braille", _("Braille only, the video waits for Enter"))]
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Descriptions in the player:")), 0, wx.ALL, 5)
        self.output_mode_choice = wx.Choice(self.audio_output_panel, choices=[c[1] for c in self.OUTPUT_MODE_CHOICES])
        audio_sizer.Add(self.output_mode_choice, 0, wx.EXPAND|wx.ALL, 5)
        self.overlay_checkbox = wx.CheckBox(self.audio_output_panel, label=_("Also show descriptions in large text on the video"))
        audio_sizer.Add(self.overlay_checkbox, 0, wx.EXPAND|wx.ALL, 5)
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Text size on the video:")), 0, wx.ALL, 5)
        self.overlay_size_spin = wx.SpinCtrl(self.audio_output_panel, value="40", min=16, max=120)
        audio_sizer.Add(self.overlay_size_spin, 0, wx.EXPAND|wx.ALL, 5)
        self.OVERLAY_COLOR_CHOICES = [("yellow", _("Yellow")), ("white", _("White")), ("cyan", _("Cyan"))]
        audio_sizer.Add(wx.StaticText(self.audio_output_panel, label=_("Text colour on the video:")), 0, wx.ALL, 5)
        self.overlay_color_choice = wx.Choice(self.audio_output_panel, choices=[c[1] for c in self.OVERLAY_COLOR_CHOICES])
        audio_sizer.Add(self.overlay_color_choice, 0, wx.EXPAND|wx.ALL, 5)
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
        self.model_override_text.Bind(wx.EVT_TEXT, self.OnSettingChanged)
        self.model_override_text.Bind(wx.EVT_COMBOBOX, self.OnSettingChanged)
        self.refresh_models_button.Bind(wx.EVT_BUTTON, self.OnRefreshModels)
        self.test_key_button.Bind(wx.EVT_BUTTON, self.OnTestKey)
        self.desc_lang_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.thinking_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.silence_detection_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.style_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.emphasis_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.max_words_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.colors_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.voice_gain_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.second_voice_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.gender_voice_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        for choice in self.piper_choices.values():
            choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.output_mode_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.overlay_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.overlay_size_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.overlay_color_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.explicitation_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.quality_check_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.translate_dialogue_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.media_resolution_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.send_silenced_video_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.disable_safety_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.enable_chunking_checkbox.Bind(wx.EVT_CHECKBOX, self.OnSettingChanged)
        self.chunk_duration_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.tts_engine_choice.Bind(wx.EVT_RADIOBOX, self.OnTtsEngineChanged)
        self.openai_voice_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.manage_voices_button.Bind(wx.EVT_BUTTON, self.OnManageVoices)
        self.sapi_voice_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)
        self.sapi_rate_slider.Bind(wx.EVT_SLIDER, self.OnSettingChanged)
        self.ducking_spin.Bind(wx.EVT_SPINCTRL, self.OnSettingChanged)
        self.pronunciation_text.Bind(wx.EVT_TEXT, self.OnSettingChanged)
        self.ad_mode_choice.Bind(wx.EVT_CHOICE, self.OnSettingChanged)

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
        
        self.yt_quality_choice.SetStringSelection(self.current_settings.get("youtube_download_quality", config_model.DEFAULT_SETTINGS["youtube_download_quality"]))
        
        saved_lang = self.current_settings.get("application_language", "")
        try:
            self.app_lang_choice.SetSelection(self.app_lang_internal_values.index(saved_lang))
        except ValueError:
            self.app_lang_choice.SetSelection(0)
            
        self.log_level_choice.SetSelection(_safe_index(self.log_level_internal_values, self.current_settings.get("logging_level", "INFO")))
        self.interrupt_speech_checkbox.SetValue(self.current_settings.get("player_allow_speech_interruption", config_model.DEFAULT_SETTINGS["player_allow_speech_interruption"]))
        
        self.api_key_text.SetValue(self.current_settings.get("user_gemini_api_key", ""))
        self.openai_api_key_text.SetValue(self.current_settings.get("user_openai_api_key", ""))
        self.frame_rate_choice.SetSelection(_safe_index(self.frame_rate_internal_values, self.current_settings.get("frame_rate_for_ai", 0)))
        self.verbosity_choice.SetSelection(_safe_index(self.verbosity_internal_values, self.current_settings.get("gemini_description_verbosity", config.DEFAULT_VERBOSITY)))
        self._set_model_choices(list(self.model_override_text.GetItems()),
                                self.current_settings.get("gemini_model_override", ""))
        codes = [c[0] for c in self.DESCRIPTION_LANGUAGE_CHOICES]
        dl = self.current_settings.get("description_language", "")
        self.desc_lang_choice.SetSelection(codes.index(dl) if dl in codes else 0)
        levels = [c[0] for c in self.THINKING_LEVEL_CHOICES]
        tl = self.current_settings.get("gemini_thinking_level", "medium")
        self.thinking_choice.SetSelection(levels.index(tl) if tl in levels else 1)
        self.silence_detection_checkbox.SetValue(self.current_settings.get("enable_silence_detection", True))
        for ctrl, choices, key, default in ((self.style_choice, self.STYLE_CHOICES, "description_style", "objective"),
                                            (self.emphasis_choice, self.EMPHASIS_CHOICES, "description_emphasis", "general")):
            values = [c[0] for c in choices]
            value = self.current_settings.get(key, default)
            ctrl.SetSelection(values.index(value) if value in values else 0)
        self.max_words_spin.SetValue(int(self.current_settings.get("max_words_per_description", 0) or 0))
        self.colors_checkbox.SetValue(self.current_settings.get("mention_colors", True))
        self.voice_gain_spin.SetValue(int(self.current_settings.get("ad_voice_gain_db", 0) or 0))
        self.second_voice_checkbox.SetValue(self.current_settings.get("on_screen_text_second_voice", True))
        self.gender_voice_checkbox.SetValue(bool(self.current_settings.get("dialogue_voice_by_gender", True)))
        for gender, choice in self.piper_choices.items():
            voice_id = self.current_settings.get(f"piper_voice_{gender}") or ""
            if voice_id not in choice.voice_ids:  # chosen for another description language
                choice.Append(voice_id)
                choice.voice_ids.append(voice_id)
            choice.SetSelection(choice.voice_ids.index(voice_id))
        for key, ctrl in self.cloud_ctrls.items():
            ctrl.ChangeValue(str(self.current_settings.get(key) or ""))
        def select(choice, pairs, key, default):
            keys = [c[0] for c in pairs]
            value = self.current_settings.get(key, default)
            choice.SetSelection(keys.index(value) if value in keys else 0)
        select(self.output_mode_choice, self.OUTPUT_MODE_CHOICES, "player_output_mode", "screen_reader")
        select(self.overlay_color_choice, self.OVERLAY_COLOR_CHOICES, "player_overlay_color", "yellow")
        self.overlay_checkbox.SetValue(bool(self.current_settings.get("player_overlay", False)))
        self.overlay_size_spin.SetValue(int(self.current_settings.get("player_overlay_size", 40) or 40))
        values = [c[0] for c in self.EXPLICITATION_CHOICES]
        value = self.current_settings.get("description_explicitation", "name")
        self.explicitation_choice.SetSelection(values.index(value) if value in values else 1)
        self.quality_check_checkbox.SetValue(self.current_settings.get("enable_quality_check", True))
        self.translate_dialogue_checkbox.SetValue(self.current_settings.get("translate_foreign_dialogue", False))
        values = [c[0] for c in self.MEDIA_RESOLUTION_CHOICES]
        value = self.current_settings.get("gemini_media_resolution", "")
        self.media_resolution_choice.SetSelection(values.index(value) if value in values else 0)
        self.send_silenced_video_checkbox.SetValue(self.current_settings.get("send_silenced_video_to_ai", config_model.DEFAULT_SETTINGS["send_silenced_video_to_ai"]))
        self.disable_safety_checkbox.SetValue(self.current_settings.get("gemini_disable_safety_block_none", False))
        
        is_chunking = self.current_settings.get("enable_video_chunking", False)
        self.enable_chunking_checkbox.SetValue(is_chunking)
        self.chunk_duration_spin.SetValue(self.current_settings.get("video_chunk_duration_seconds", config_model.DEFAULT_SETTINGS["video_chunk_duration_seconds"]))
        self.chunk_duration_label.Enable(is_chunking)
        self.chunk_duration_spin.Enable(is_chunking)
        
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
        self.ducking_spin.SetValue(-abs(int(self.current_settings.get("mp3_audio_ducking_level_db", -9))))
        self.pronunciation_text.ChangeValue(self.current_settings.get("tts_pronunciations") or "")
        modes = [c[0] for c in self.AD_MODE_CHOICES]
        mode = self.current_settings.get("player_ad_mode") or "inline"
        self.ad_mode_choice.SetSelection(modes.index(mode) if mode in modes else 0)
        
        self.settings_changed = False
        self.apply_button.Disable()

    def OnTestKey(self, event):
        key = self.api_key_text.GetValue().strip()
        if not key:
            speak_message(_("Type or paste your Gemini API key first."), interrupt=True)
            return
        self.test_key_button.Disable()
        speak_message(_("Testing the key..."))

        def work():
            try:
                names, error = gemini.test_api_key(key), None
            except Exception as e:
                names, error = [], e
            wx.CallAfter(done, names, error)

        def done(names, error):
            if not self:
                return
            self.test_key_button.Enable()
            if error or not names:
                msg = _("The key does not work: %s") % (error or _("no models returned"))
                wx.MessageBox(msg, _("Test key"), wx.OK | wx.ICON_ERROR, self)
            else:
                speak_message(_("The key works. %d models available.") % len(names), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

    def _set_model_choices(self, names, current):
        """Fill the read-only model list and select the saved model (default when none is saved)."""
        current = (current or "").strip() or config.GEMINI_MODEL_NAME
        if current not in names:
            names = [current] + list(names)
        self.model_override_text.Set(names)
        self.model_override_text.SetStringSelection(current)

    def OnRefreshModels(self, event):
        """List the models this API key can use (google-genai models.list) in a thread."""
        self.refresh_models_button.Disable()
        speak_message(_("Loading model list..."))

        def work():
            try:
                names, error = gemini.list_generation_models(), None
            except Exception as e:
                names, error = [], e
            wx.CallAfter(done, names, error)

        def done(names, error):
            if not self:
                return
            self.refresh_models_button.Enable()
            if error or not names:
                speak_message(_("Could not load the model list: %s") % (error or _("no models returned")), interrupt=True)
                return
            self._set_model_choices(names, self.model_override_text.GetValue())
            speak_message(_("%d models available.") % len(names), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

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
            if not (ok and sound_player.play_voice(path)):
                speak_message(_("The voice could not be prepared: %s") % (error or _("no sound produced")), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

    def ApplySettings(self):
        self.current_settings["youtube_download_quality"] = self.yt_quality_choice.GetStringSelection()
        self.current_settings["application_language"] = self.app_lang_internal_values[self.app_lang_choice.GetSelection()]
        self.current_settings["logging_level"] = self.log_level_internal_values[self.log_level_choice.GetSelection()]
        self.current_settings["player_allow_speech_interruption"] = self.interrupt_speech_checkbox.IsChecked()
        
        self.current_settings["user_gemini_api_key"] = self.api_key_text.GetValue().strip()
        self.current_settings["user_openai_api_key"] = self.openai_api_key_text.GetValue().strip()
        self.current_settings["frame_rate_for_ai"] = self.frame_rate_internal_values[self.frame_rate_choice.GetSelection()]
        self.current_settings["gemini_description_verbosity"] = self.verbosity_internal_values[self.verbosity_choice.GetSelection()]
        self.current_settings["gemini_model_override"] = self.model_override_text.GetValue().strip()
        self.current_settings["description_language"] = self.DESCRIPTION_LANGUAGE_CHOICES[self.desc_lang_choice.GetSelection()][0]
        self.current_settings["gemini_thinking_level"] = self.THINKING_LEVEL_CHOICES[self.thinking_choice.GetSelection()][0]
        self.current_settings["enable_silence_detection"] = self.silence_detection_checkbox.IsChecked()
        self.current_settings["description_style"] = self.STYLE_CHOICES[self.style_choice.GetSelection()][0]
        self.current_settings["description_emphasis"] = self.EMPHASIS_CHOICES[self.emphasis_choice.GetSelection()][0]
        self.current_settings["max_words_per_description"] = self.max_words_spin.GetValue()
        self.current_settings["mention_colors"] = self.colors_checkbox.IsChecked()
        self.current_settings["ad_voice_gain_db"] = self.voice_gain_spin.GetValue()
        self.current_settings["on_screen_text_second_voice"] = self.second_voice_checkbox.IsChecked()
        self.current_settings["dialogue_voice_by_gender"] = self.gender_voice_checkbox.IsChecked()
        for gender, choice in self.piper_choices.items():
            self.current_settings[f"piper_voice_{gender}"] = choice.voice_ids[max(0, choice.GetSelection())]
        for key, ctrl in self.cloud_ctrls.items():
            self.current_settings[key] = ctrl.GetValue().strip()
        self.current_settings["player_output_mode"] = self.OUTPUT_MODE_CHOICES[self.output_mode_choice.GetSelection()][0]
        self.current_settings["player_overlay"] = self.overlay_checkbox.IsChecked()
        self.current_settings["player_overlay_size"] = self.overlay_size_spin.GetValue()
        self.current_settings["player_overlay_color"] = self.OVERLAY_COLOR_CHOICES[self.overlay_color_choice.GetSelection()][0]
        self.current_settings["description_explicitation"] = self.EXPLICITATION_CHOICES[self.explicitation_choice.GetSelection()][0]
        self.current_settings["enable_quality_check"] = self.quality_check_checkbox.IsChecked()
        self.current_settings["translate_foreign_dialogue"] = self.translate_dialogue_checkbox.IsChecked()
        self.current_settings["gemini_media_resolution"] = self.MEDIA_RESOLUTION_CHOICES[self.media_resolution_choice.GetSelection()][0]
        self.current_settings["send_silenced_video_to_ai"] = self.send_silenced_video_checkbox.IsChecked()
        self.current_settings["gemini_disable_safety_block_none"] = self.disable_safety_checkbox.IsChecked()
        self.current_settings["enable_video_chunking"] = self.enable_chunking_checkbox.IsChecked()
        self.current_settings["video_chunk_duration_seconds"] = self.chunk_duration_spin.GetValue()
        
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
        self.current_settings["tts_pronunciations"] = self.pronunciation_text.GetValue()
        self.current_settings["player_ad_mode"] = self.AD_MODE_CHOICES[self.ad_mode_choice.GetSelection()][0]
        
        config_model.app_settings.update(self.current_settings)
        if config_model.save_settings(config_model.app_settings):
            gemini.reset_gemini_client()  # a new key or model must be used from the next request
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
