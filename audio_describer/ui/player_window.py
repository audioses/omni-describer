# audio_describer/ui/player_window.py
from ..i18n_setup import _
import wx
import sys
import os
import sys
import ctypes

# --- START OF VLC BUNDLING FIX ---
vlc = None
VLC_AVAILABLE = False

def _vlc_loader_log(level, message):
    """Minimal logger for early-stage debugging before main logger is ready."""
    try:
        from ..utils.logger import app_logger
        getattr(app_logger, level)(message)
    except (ImportError, AttributeError):
        print(f"VLC LOADER [{level.upper()}]: {message}")

def _setup_vlc_environment():
    """
    Ensures VLC loads from the bundled 'bin/vlc' directory when frozen.
    Sets environment variables so python-vlc never falls back to Program Files.
    """
    global vlc, VLC_AVAILABLE

    if VLC_AVAILABLE:
        return  # Already initialized

    try:
        vlc_dir = None

        # Detect frozen build (PyInstaller)
        if getattr(sys, 'frozen', False):
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            vlc_dir = os.path.join(base_path, 'bin', 'vlc')
            _vlc_loader_log("info", f"Running in frozen mode. Base path: {base_path}")
        else:
            # For non-frozen dev/debug mode, look in local folder
            vlc_dir = os.path.join(os.path.dirname(__file__), '..', 'bin', 'vlc')
            vlc_dir = os.path.abspath(vlc_dir)
            _vlc_loader_log("info", f"Running in script mode. VLC path: {vlc_dir}")

        if not os.path.isdir(vlc_dir):
            _vlc_loader_log("error", f"VLC directory not found: {vlc_dir}")
            VLC_AVAILABLE = False
            return

        libvlc_path = os.path.join(vlc_dir, 'libvlc.dll')
        plugins_path = os.path.join(vlc_dir, 'plugins')

        _vlc_loader_log("info", f"libvlc.dll: {libvlc_path}  Exists: {os.path.isfile(libvlc_path)}")
        _vlc_loader_log("info", f"Plugins path: {plugins_path}  Exists: {os.path.isdir(plugins_path)}")

        # Ensure DLL directory is searched (Windows 3.8+)
        if sys.platform == "win32" and sys.version_info >= (3, 8):
            os.add_dll_directory(vlc_dir)
            _vlc_loader_log("info", f"Added '{vlc_dir}' to DLL search path.")

        # Tell python-vlc exactly where to find libvlc.dll
        os.environ['PYTHON_VLC_LIB_PATH'] = libvlc_path
        # Tell VLC where to find its plugins
        os.environ['VLC_PLUGIN_PATH'] = plugins_path

        # Preload libvlc.dll so python-vlc uses it
        if os.path.isfile(libvlc_path):
            ctypes.CDLL(libvlc_path)
            _vlc_loader_log("info", f"Preloaded VLC library from: {libvlc_path}")
        else:
            _vlc_loader_log("error", "libvlc.dll not found, VLC will be unavailable.")
            VLC_AVAILABLE = False
            return

        # Finally, import python-vlc
        import vlc as vlc_module
        vlc = vlc_module
        VLC_AVAILABLE = True
        _vlc_loader_log("info", "Successfully imported python-vlc module.")

    except (ImportError, OSError, Exception) as e:
        _vlc_loader_log("error", f"Failed to load VLC: {e}")
        VLC_AVAILABLE = False
        vlc = None

# Initialize on import
_setup_vlc_environment()
# --- END OF VLC BUNDLING FIX ---



from ..utils.logger import app_logger
from ..core import video_processor, timing
import threading
from ..core import audio_describer
from ..models import project_model
from .accessibility_utils import speak_message, braille_message, set_control_accessible_name
from .player_voice import VoiceCache
from .edit_description_dialog import EditDescriptionDialog
from .ask_more_dialog import AskMoreSubmitEvent, EVT_ASK_MORE_SUBMIT, AskMoreAddToMainEvent, EVT_ASK_MORE_ADD_TO_MAIN
from . import player_export
from . import player_vqa
from .settings_dialog import SettingsDialog
from ..models import config_model
from .. import config
from ..utils import sound_player

ID_PLAY_PAUSE = wx.NewIdRef()
ID_EDIT_DESCRIPTIONS = wx.NewIdRef()
ID_EXPORT = wx.NewIdRef() # Consolidated export button ID
ID_MEDIA_TIMER = wx.NewIdRef()
ID_SEEK_BACK = wx.NewIdRef()
ID_SEEK_FWD = wx.NewIdRef()
ID_ASK_MORE = wx.NewIdRef()
ID_EXPLORE_SCENE = wx.NewIdRef()
ID_SETTINGS = wx.NewIdRef()

STATIC_SEEK_BUTTON_INTERVAL_MS = 5000
LONG_SEEK_INTERVAL_MS = 30000
DUCK_DOWN_MS = 150   # Netflix AD guide: smooth dips, never abrupt
DUCK_UP_MS = 400
RAMP_STEP_MS = 20


AD_MODES = ("inline", "extended", "on_demand")
OVERLAY_COLORS = {"yellow": 0xFFFF00, "white": 0xFFFFFF, "cyan": 0x00FFFF}
MAX_DUCKING_DB = 24


def _shortcuts_help_text():
    return _(
        "Player shortcuts: Space, play or pause. Left and Right arrows, back or forward 5 seconds; "
        "with Control, 30 seconds. Alt plus Left or Right, previous or next description. "
        "Up and Down arrows, video volume; with Shift, how much the video is lowered during descriptions. R, repeat the last description. T, announce the time. "
        "A, turn audio description on or off. D, change description density. "
        "M, change the audio description mode: in the pauses, pause the video when needed, or on demand. "
        "In on-demand mode, Enter describes the current moment and Shift+Enter gives more detail. "
        "I, audio introduction: the main characters and places, without spoilers; Shift+I saves it as an MP3. V, quality report. "
        "W, what is on screen now; ask follow-up questions in the window that opens. E, evaluate this audio description. "
        "Q, ask more. S, explore scene. L, next audio track of the video; C, next subtitle track or none. "
        "In on-demand mode, a soft sound tells you an essential description is available. "
        "In braille-only output, Enter resumes the video after each description. "
        "Control plus S, save project. F1 or question mark, this help."
    )


class PlayerWindow(wx.Dialog):
    def __init__(self, parent, video_path, descriptions, is_temp_video=False, description_token_usage=None, character_glossary=None, title=_("Video Player with Descriptions"), project_path=None):
        super().__init__(parent, title=title, size=(800, 750), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL)
        self.video_path = video_path
        self.descriptions = descriptions
        self.is_temp_video = is_temp_video
        self.parent_frame = parent
        self.currently_playing_description_index = -1
        self._is_closing = False
        self._user_is_dragging_slider = False
        self.ask_more_dialog_instance = None
        self._original_state_before_ask_more = None
        self.progress_dialog = None
        self.description_token_usage = description_token_usage if description_token_usage is not None else []
        self.character_glossary = character_glossary if character_glossary is not None else []
        self.vqa_token_usage = []

        # Projects: a fresh generation is unsaved work; an opened project is clean.
        self.project_path = project_path
        self._project_video_path = video_path if project_path else None
        self._dirty = project_path is None and bool(descriptions)
        self.audio_introduction = ""
        self.evaluation = {}
        if project_path:
            try:
                extras = project_model.load_project_extras(project_path)
                self.audio_introduction, self.evaluation = extras["audio_introduction"], extras["evaluation"]
            except (OSError, ValueError) as e:
                app_logger.warning(f"Could not read audio introduction / evaluation: {e}")
        self._intro_busy = False

        # Playback state for shortcuts and ducking
        self._last_tick_sec = 0.0
        self._last_spoken_text = ""
        self._ad_enabled = bool(config_model.get_setting("player_ad_enabled"))
        self._duck_token = 0
        self._current_volume = None
        self._pause_token = 0  # bumps on user play/pause, cancels a pending auto-resume
        self._braille_hold = False  # braille-only output: video paused until Enter
        self._voice = None  # app voice clips, created when that output is chosen
        self._ad_mode = config_model.get_setting("player_ad_mode") or "inline"
        if self._ad_mode not in AD_MODES:
            self._ad_mode = "inline"
        density_by_verbosity = {config.VERBOSITY_SHORT: 1, config.VERBOSITY_STANDARD: 2, config.VERBOSITY_DETAILED: 3}
        self._density = density_by_verbosity.get(config_model.get_setting("gemini_description_verbosity"), 2)
        self._refresh_playlist()

        self.vlc_instance = None
        self.vlc_player = None
        self.vlc_events = None
        if not VLC_AVAILABLE:
            wx.MessageBox(_("VLC library is not installed or could not be loaded. Playback will be unavailable."), _("Error"), wx.OK | wx.ICON_ERROR)
        
        self.InitUI()
        self.CentreOnParent()
        self.Layout()

        wx.CallAfter(self.play_pause_button.SetFocus)
        
        if VLC_AVAILABLE:
            self.setup_vlc()
            self.update_token_usage_display()
        else:
            self.disable_media_dependent_controls()

    def InitUI(self):
        self.panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.video_panel = wx.Panel(self.panel, style=wx.NO_BORDER)
        self.video_panel.SetBackgroundColour(wx.BLACK)
        main_sizer.Add(self.video_panel, 5, wx.EXPAND | wx.ALL, 5)

        self.time_display_label = wx.StaticText(self.panel, label="00:00 / 00:00")
        main_sizer.Add(self.time_display_label, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 2)

        slider_play_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.play_pause_button = wx.Button(self.panel, ID_PLAY_PAUSE, _("Play"))
        slider_play_sizer.Add(self.play_pause_button, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        self.seek_slider = wx.Slider(self.panel, value=0, minValue=0, maxValue=1000, style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS)
        set_control_accessible_name(self.seek_slider, _("Video progress slider"))
        slider_play_sizer.Add(self.seek_slider, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(slider_play_sizer, 0, wx.EXPAND | wx.ALL, 2)

        buttons_volume_sizer = wx.BoxSizer(wx.HORIZONTAL)
        seek_s = STATIC_SEEK_BUTTON_INTERVAL_MS // 1000
        self.seek_back_button = wx.Button(self.panel, ID_SEEK_BACK, _("&Rewind %ss") % seek_s)
        buttons_volume_sizer.Add(self.seek_back_button, 1, wx.EXPAND | wx.ALL, 5)
        self.seek_fwd_button = wx.Button(self.panel, ID_SEEK_FWD, _("&Forward %ss") % seek_s)
        buttons_volume_sizer.Add(self.seek_fwd_button, 1, wx.EXPAND | wx.ALL, 5)
        buttons_volume_sizer.AddStretchSpacer(1)
        buttons_volume_sizer.Add(wx.StaticText(self.panel, label=_("Vol:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        initial_volume = int(config_model.get_setting("player_volume_percent"))
        self.volume_slider = wx.Slider(self.panel, value=initial_volume, minValue=0, maxValue=100, size=(100, -1))
        
        buttons_volume_sizer.Add(self.volume_slider, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        main_sizer.Add(buttons_volume_sizer, 0, wx.EXPAND | wx.ALL, 5)

        main_sizer.Add(wx.StaticText(self.panel, label=_("Current Audio Description:")), 0, wx.LEFT | wx.TOP, 5)
        self.current_desc_text_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_STATIC)
        self.current_desc_text_ctrl.SetMinSize((-1, 60))
        main_sizer.Add(self.current_desc_text_ctrl, 1, wx.EXPAND | wx.ALL, 5)

        token_usage_label = wx.StaticText(self.panel, label=_("AI Token Usage:"))
        main_sizer.Add(token_usage_label, 0, wx.LEFT | wx.TOP | wx.EXPAND, 5)
        self.token_usage_text_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_STATIC)
        self.token_usage_text_ctrl.SetMinSize((-1, 80))
        main_sizer.Add(self.token_usage_text_ctrl, 0, wx.EXPAND | wx.ALL, 5)

        action_buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.explore_scene_button = wx.Button(self.panel, ID_EXPLORE_SCENE, _("Explore Scene..."))
        action_buttons_sizer.Add(self.explore_scene_button, 0, wx.ALL, 5)
        self.ask_more_button = wx.Button(self.panel, ID_ASK_MORE, _("Ask More..."))
        action_buttons_sizer.Add(self.ask_more_button, 0, wx.ALL, 5)
        self.edit_descriptions_button = wx.Button(self.panel, ID_EDIT_DESCRIPTIONS, _("Edit Descriptions..."))
        action_buttons_sizer.Add(self.edit_descriptions_button, 0, wx.ALL, 5)
        
        self.settings_button = wx.Button(self.panel, ID_SETTINGS, _("Settings..."))
        action_buttons_sizer.Add(self.settings_button, 0, wx.ALL, 5)

        action_buttons_sizer.AddStretchSpacer(1)
        self.export_button = wx.Button(self.panel, ID_EXPORT, _("Export..."))
        action_buttons_sizer.Add(self.export_button, 0, wx.ALL, 5)
        main_sizer.Add(action_buttons_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.panel.SetSizerAndFit(main_sizer)
        self.timer = wx.Timer(self, ID_MEDIA_TIMER)
        self.BindAllEvents()

    def setup_vlc(self):
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_player_new()
        
        self.vlc_player.set_hwnd(self.video_panel.GetHandle())
        
        media = self.vlc_instance.media_new(self.video_path)
        self.vlc_player.set_media(media)
        
        self.vlc_player.audio_set_volume(self.volume_slider.GetValue())
        
        self.vlc_events = self.vlc_player.event_manager()
        self.vlc_events.event_attach(vlc.EventType.MediaPlayerPlaying, self.OnVlcMediaPlay)
        self.vlc_events.event_attach(vlc.EventType.MediaPlayerPaused, self.OnVlcMediaPause)
        self.vlc_events.event_attach(vlc.EventType.MediaPlayerEndReached, self.OnVlcMediaFinished)
        
        self.play_pause_button.Enable()
        self.seek_slider.Enable()
        self.seek_back_button.Enable()
        self.seek_fwd_button.Enable()
        self.ask_more_button.Enable()
        self.explore_scene_button.Enable()
        self.settings_button.Enable()
        if self.descriptions: self.edit_descriptions_button.Enable()
        self.timer.Start(250)
        
    def BindAllEvents(self):
        self.Bind(player_export.EVT_EXPORT_DONE, self.OnExportDone)
        self.Bind(wx.EVT_BUTTON, self.OnPlayPause, id=ID_PLAY_PAUSE)
        self.Bind(wx.EVT_BUTTON, self.OnExploreScene, id=ID_EXPLORE_SCENE)
        self.Bind(wx.EVT_BUTTON, self.OnAskMore, id=ID_ASK_MORE)
        self.Bind(wx.EVT_BUTTON, self.OnEditDescriptionsClick, id=ID_EDIT_DESCRIPTIONS)
        self.Bind(wx.EVT_BUTTON, self.OnSeekBack, id=ID_SEEK_BACK)
        self.Bind(wx.EVT_BUTTON, self.OnSeekFwd, id=ID_SEEK_FWD)
        
        self.Bind(wx.EVT_BUTTON, self.OnSettings, id=ID_SETTINGS)
        self.Bind(wx.EVT_BUTTON, self.OnExport, id=ID_EXPORT)
        
        self.seek_slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.OnSeekSliderThumbTrack)
        self.seek_slider.Bind(wx.EVT_SCROLL_THUMBRELEASE, self.OnSeekSliderThumbRelease)
        self.seek_slider.Bind(wx.EVT_SCROLL_CHANGED, self.OnSeekSliderKeyboardChange)
        self.volume_slider.Bind(wx.EVT_SLIDER, self.OnVolumeChanged)
        
        self.Bind(wx.EVT_TIMER, self.OnTimer, id=ID_MEDIA_TIMER)
        self.Bind(wx.EVT_CLOSE, self.OnCloseWindow)
        self.Bind(wx.EVT_CHAR_HOOK, self.OnCharHook)
        
        self.Bind(EVT_ASK_MORE_SUBMIT, self.OnAskMoreSubmitted)
        self.Bind(EVT_ASK_MORE_ADD_TO_MAIN, self.OnAddToMainDescription)
        
    def _refresh_playlist(self):
        """Descriptions actually played: filtered by density level (CAB/AMI), no regeneration."""
        self._playlist = timing.filter_by_level(self.descriptions, self._density)
        self.currently_playing_description_index = -1
        if config_model.get_setting("player_output_mode") == "app_voice":
            self._voice = self._voice or VoiceCache()
            self._voice.prepare([(d[2], timing.voice_gender(d)) for d in self._playlist])

    def OnPlayPause(self, event):
        if not self.vlc_player or self._is_closing:
            return
        self._pause_token += 1
        self._braille_hold = False

        state = self.vlc_player.get_state()
        app_logger.info(f"Play button clicked. Current VLC state: {state}")

        if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            app_logger.info("Player is stopped/ended. Resetting and playing.")
            self.vlc_player.stop()
            self.vlc_player.play()
        elif state == vlc.State.NothingSpecial:
            app_logger.info("Player has nothing special, starting playback.")
            self.vlc_player.play()
        else:
            app_logger.info("Player is playing/paused, toggling pause.")
            self.vlc_player.pause()

    def OnVlcMediaPlay(self, event):
        wx.CallAfter(self.play_pause_button.SetLabel, _("Pause"))

    def OnVlcMediaPause(self, event):
        wx.CallAfter(self.play_pause_button.SetLabel, _("Play"))

    def OnVlcMediaFinished(self, event):
        wx.CallAfter(self.play_pause_button.SetLabel, _("Replay"))
        self.currently_playing_description_index = -1
        wx.CallAfter(self.seek_slider.SetValue, 1000)
        wx.CallAfter(self.OnTimer, None)

    def OnSeekBack(self, event):
        if not self.vlc_player: return
        current_time = self.vlc_player.get_time()
        self.vlc_player.set_time(max(0, current_time - STATIC_SEEK_BUTTON_INTERVAL_MS))

    def OnSeekFwd(self, event):
        if not self.vlc_player: return
        current_time = self.vlc_player.get_time()
        self.vlc_player.set_time(current_time + STATIC_SEEK_BUTTON_INTERVAL_MS)

    def OnSeekSliderThumbTrack(self, event):
        if not self.vlc_player: return
        self._user_is_dragging_slider = True
        media_length_ms = self.vlc_player.get_length()
        if media_length_ms > 0:
            pos = self.seek_slider.GetValue() / 1000.0
            current_time_ms = int(media_length_ms * pos)
            self.time_display_label.SetLabel(f"{self.format_time_for_display(current_time_ms)} / {self.format_time_for_display(media_length_ms)}")
        
    def OnSeekSliderThumbRelease(self, event):
        if not self.vlc_player: return
        pos = self.seek_slider.GetValue() / 1000.0
        self.vlc_player.set_position(pos)
        self._user_is_dragging_slider = False
        wx.CallLater(100, self.OnTimer, None)

    def OnSeekSliderKeyboardChange(self, event):
        if self._user_is_dragging_slider: return
        self.OnSeekSliderThumbRelease(event)

    def OnVolumeChanged(self, event):
        if not self.vlc_player: return
        new_volume = self.volume_slider.GetValue()
        self._duck_token += 1  # user volume wins over any ducking in progress
        self._set_vlc_volume(new_volume)
        config_model.app_settings["player_volume_percent"] = new_volume
        config_model.save_settings(config_model.app_settings)

    # --- Ducking: lower the programme while a description is spoken (never mute) ---

    def _set_vlc_volume(self, volume):
        if self.vlc_player:
            self.vlc_player.audio_set_volume(int(volume))
            self._current_volume = int(volume)

    def _ramp_volume(self, target, duration_ms, token, on_done=None):
        start = self._current_volume if self._current_volume is not None else self.volume_slider.GetValue()
        steps = max(1, duration_ms // RAMP_STEP_MS)

        def step(i):
            if token != self._duck_token or self._is_closing or not self.vlc_player:
                return
            self._set_vlc_volume(round(start + (target - start) * i / steps))
            if i < steps:
                wx.CallLater(RAMP_STEP_MS, step, i + 1)
            elif on_done:
                on_done()
        step(1)

    def _duck_for_description(self, text, seconds=None):
        attenuation_db = abs(config_model.get_setting("mp3_audio_ducking_level_db") or 0)
        if attenuation_db <= 0 or not self.vlc_player:
            return
        self._duck_token += 1
        token = self._duck_token
        base = self.volume_slider.GetValue()
        # A screen reader does not report when it finishes speaking: estimate at the
        # AD reference rate plus a margin.
        hold_ms = int((seconds or timing.estimate_speech_seconds(text)) * 1000) + 300

        def restore():
            wx.CallLater(hold_ms, lambda: self._ramp_volume(self.volume_slider.GetValue(), DUCK_UP_MS, token))
        self._ramp_volume(timing.db_to_volume(base, attenuation_db), DUCK_DOWN_MS, token, on_done=restore)

    def _announce_description(self, index, interrupt=None):
        desc = self._playlist[index]
        text = desc[2]
        self.currently_playing_description_index = index
        self.current_desc_text_ctrl.SetValue(text)
        if not self._ad_enabled:
            return
        if self._ad_mode == "on_demand":
            if timing.level_of(desc) == 1:  # Describe Now study: users did not know when to ask
                sound_player.play("going.mp3")
            return
        if interrupt is None:
            interrupt = config_model.get_setting("player_allow_speech_interruption")
        self._last_spoken_text = text
        if timing.is_read_text(desc):
            # ADLAB §2.2.3: a short earcon announces on-screen text / translated dialogue instead of words.
            sound_player.play("start.mp3")
        seconds = self._deliver(text, interrupt)
        if not (self.vlc_player and self.vlc_player.is_playing()):
            return
        if config_model.get_setting("player_output_mode") == "braille":
            # Bernard (audit §3): read at his own pace, Enter resumes.
            self.vlc_player.set_pause(1)
            self._pause_token += 1
            self._braille_hold = True
        elif self._ad_mode == "extended" and timing.needs_extended_pause(desc):
            self._auto_pause_for(text, seconds)
        else:
            self._duck_for_description(text, seconds)

    def _deliver(self, text, interrupt=True):
        """Audit §6.2 output modes. Returns the real duration in seconds when the app voice spoke it."""
        mode = config_model.get_setting("player_output_mode")
        clip = self._voice.get(text) if (mode == "app_voice" and self._voice) else None
        seconds = None
        if mode == "braille":
            braille_message(text)
        elif clip and sound_player.play_voice(clip[0]):
            seconds = clip[1]
        else:  # screen reader, or a clip not synthesised yet
            speak_message(text, interrupt=interrupt)
        self._show_overlay(text, seconds or timing.estimate_speech_seconds(text))
        return seconds

    def _show_overlay(self, text, seconds):
        """Low vision (Léa, audit §3.3): large text on the video through VLC's marquee filter."""
        if not config_model.get_setting("player_overlay") or not self.vlc_player:
            return
        try:
            m = vlc.VideoMarqueeOption
            color = OVERLAY_COLORS.get(config_model.get_setting("player_overlay_color"), OVERLAY_COLORS["yellow"])
            self.vlc_player.video_set_marquee_int(m.Enable, 1)
            self.vlc_player.video_set_marquee_int(m.Size, int(config_model.get_setting("player_overlay_size") or 40))
            self.vlc_player.video_set_marquee_int(m.Color, color)
            self.vlc_player.video_set_marquee_int(m.Position, 8)  # bottom centre
            self.vlc_player.video_set_marquee_int(m.Timeout, int(seconds * 1000) + 1500)
            self.vlc_player.video_set_marquee_string(m.Text, timing.overlay_text(text))
        except Exception as e:
            app_logger.warning(f"Overlay text failed: {e}")

    def AdjustDucking(self, delta_db):
        """Shift+Up/Down (audit §6.1): make the audio description stand out more or less."""
        db = max(0, min(MAX_DUCKING_DB, abs(int(config_model.get_setting("mp3_audio_ducking_level_db") or 0)) + delta_db))
        config_model.app_settings["mp3_audio_ducking_level_db"] = -db
        config_model.save_settings(config_model.app_settings)
        speak_message(_("Video lowered by %d dB during descriptions.") % db if db else
                      _("Video not lowered during descriptions."), interrupt=True)

    def _auto_pause_for(self, text, seconds=None):
        """Extended AD (WCAG 1.2.7): pause, let the description be spoken, resume."""
        self.vlc_player.set_pause(1)
        self._pause_token += 1
        token = self._pause_token
        resume_ms = int((seconds or timing.estimate_speech_seconds(text)) * 1000) + 300

        def resume():
            if token == self._pause_token and self.vlc_player and not self._is_closing:
                self.vlc_player.set_pause(0)
        wx.CallLater(resume_ms, resume)

    def OnTimer(self, event):
        if self._is_closing or not self.vlc_player: return

        if not self._user_is_dragging_slider:
            current_pos_ms = self.vlc_player.get_time()
            media_length_ms = self.vlc_player.get_length()
            if media_length_ms <= 0: return

            pos_float = self.vlc_player.get_position()
            self.seek_slider.SetValue(int(pos_float * 1000))
            self.time_display_label.SetLabel(f"{self.format_time_for_display(current_pos_ms)} / {self.format_time_for_display(media_length_ms)}")

            current_pos_sec = current_pos_ms / 1000.0
            # B12: also catch a short description that began and ended between two ticks.
            index = timing.crossed_description_index(self._playlist, self._last_tick_sec, current_pos_sec)
            self._last_tick_sec = current_pos_sec

            if index != -1:
                if index != self.currently_playing_description_index:
                    if self.vlc_player.is_playing():
                        self._announce_description(index)
                    else:
                        # Paused/seeking: just show the text
                        self.currently_playing_description_index = index
                        self.current_desc_text_ctrl.SetValue(self._playlist[index][2])
            elif self.currently_playing_description_index != -1:
                self.currently_playing_description_index = -1
                self.current_desc_text_ctrl.SetValue("")

    # --- Keyboard shortcuts (work whatever control has focus) ---

    def OnCharHook(self, event):
        key = event.GetKeyCode()
        ctrl, alt, shift = event.ControlDown(), event.AltDown(), event.ShiftDown()
        focus = wx.Window.FindFocus()
        # Read-only text fields keep their own reading keys (arrows, Ctrl+arrows, Space).
        in_text = isinstance(focus, wx.TextCtrl)
        on_volume = focus is self.volume_slider
        plain = not (ctrl or alt or shift)

        if key == wx.WXK_F1 and plain:
            speak_message(_shortcuts_help_text(), interrupt=True)
        elif ctrl and not alt and key == ord('S'):
            self.SaveProject(save_as=shift)
        elif alt and not ctrl and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            self.JumpToDescription(forward=(key == wx.WXK_RIGHT))
        elif not self.vlc_player or in_text:
            event.Skip()
        elif not (ctrl or alt) and (event.GetUnicodeKey() == ord('?') or (shift and key in (ord(','), ord('/')))):
            speak_message(_shortcuts_help_text(), interrupt=True)
        elif key in (wx.WXK_UP, wx.WXK_DOWN) and shift and not (ctrl or alt):
            self.AdjustDucking(3 if key == wx.WXK_UP else -3)
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self._braille_hold and plain:
            self._braille_hold = False
            self.vlc_player.set_pause(0)
        elif key == wx.WXK_SPACE and plain:
            self.OnPlayPause(None)
        elif key in (wx.WXK_LEFT, wx.WXK_RIGHT) and not alt and not shift and not on_volume:
            step = LONG_SEEK_INTERVAL_MS if ctrl else STATIC_SEEK_BUTTON_INTERVAL_MS
            self.SeekRelative(step if key == wx.WXK_RIGHT else -step)
        elif key in (wx.WXK_UP, wx.WXK_DOWN) and plain and not on_volume:
            delta = 5 if key == wx.WXK_UP else -5
            self.volume_slider.SetValue(max(0, min(100, self.volume_slider.GetValue() + delta)))
            self.OnVolumeChanged(None)
            speak_message(_("Volume %d") % self.volume_slider.GetValue(), interrupt=True)
        elif plain and key == ord('R'):
            self._deliver(self._last_spoken_text or _("No description yet."))
        elif plain and key == ord('T'):
            self.AnnounceTime()
        elif plain and key == ord('A'):
            self.ToggleAudioDescription()
        elif plain and key == ord('D'):
            self.CycleDensity()
        elif plain and key == ord('M'):
            self.CycleADMode()
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self._ad_mode == "on_demand" and not (ctrl or alt):
            self.DescribeNow(detailed=shift)
        elif plain and key == ord('Q'):
            self.OnAskMore(None)
        elif plain and key == ord('S'):
            self.OnExploreScene(None)
        elif plain and key == ord('I'):
            self.PlayAudioIntroduction()
        elif shift and not (ctrl or alt) and key == ord('I'):
            self.SaveAudioIntroduction()
        elif plain and key == ord('V'):
            self.AnnounceQualityReport()
        elif plain and key == ord('W'):
            self.WhatsOnScreen()
        elif plain and key == ord('E'):
            self.OpenEvaluation()
        elif plain and key == ord('L'):
            self.CycleTrack(audio=True)
        elif plain and key == ord('C'):
            self.CycleTrack(audio=False)
        else:
            event.Skip()

    def CycleTrack(self, audio=True):
        """EN 301 549 §7.1.1 / §7.2.1: choose the source's audio track (incl. an existing AD) or subtitles."""
        player = self.vlc_player
        try:
            tracks = list((player.audio_get_track_description() if audio else player.video_get_spu_description()) or [])
            current = player.audio_get_track() if audio else player.video_get_spu()
        except Exception as e:
            app_logger.warning(f"Track listing failed: {e}")
            tracks, current = [], None
        if audio:
            tracks = [t for t in tracks if t[0] != -1]  # -1 = "Disable": never mute the programme by accident
        if len(tracks) < (2 if audio else 1):
            speak_message(_("This video has only one audio track.") if audio else _("This video has no subtitles."), interrupt=True)
            return
        ids = [t[0] for t in tracks]
        track_id, name = tracks[(ids.index(current) + 1) % len(ids) if current in ids else 0]
        if audio:
            player.audio_set_track(track_id)
        else:
            player.video_set_spu(track_id)
        name = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        speak_message((_("Audio track: %s") if audio else _("Subtitles: %s")) % name, interrupt=True)

    def SeekRelative(self, delta_ms):
        if not self.vlc_player: return
        length = self.vlc_player.get_length()
        target = max(0, self.vlc_player.get_time() + delta_ms)
        if length > 0:
            target = min(target, length - 500)
        self.vlc_player.set_time(int(target))
        self._last_tick_sec = target / 1000.0  # a jump is not a crossing
        wx.CallLater(100, self.OnTimer, None)

    def CycleDensity(self):
        self._density = self._density % 3 + 1
        self._refresh_playlist()
        names = {1: _("Density low: essential descriptions only."),
                 2: _("Density medium: essential and important descriptions."),
                 3: _("Density high: all descriptions, including details.")}
        speak_message(names[self._density] + " " + _("%d descriptions.") % len(self._playlist), interrupt=True)

    def SaveAudioIntroduction(self):
        """Shift+I: save the audio introduction as an MP3 to publish next to the video."""
        if not self.audio_introduction:
            speak_message(_("No audio introduction yet. Press I to write it first."), interrupt=True)
            return
        default = os.path.splitext(os.path.basename(self.video_path or "video"))[0] + "_introduction.mp3"
        with wx.FileDialog(self, _("Save the Audio Introduction"), defaultFile=default, wildcard="MP3 (*.mp3)|*.mp3",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        text = self.audio_introduction
        speak_message(_("Saving the audio introduction..."), interrupt=True)

        def work():
            try:
                player_export.export_intro_mp3(text, path)
                error = None
            except Exception as e:
                error = e
            wx.CallAfter(done, error)

        def done(error):
            if self._is_closing:
                return
            if error:
                app_logger.error(f"Audio introduction export failed: {error}")
                wx.MessageBox(str(error), _("Export Error"), wx.OK | wx.ICON_ERROR, self)
            else:
                speak_message(_("Audio introduction saved."), interrupt=True)

        threading.Thread(target=work, daemon=True).start()

    def PlayAudioIntroduction(self):
        """Ofcom §4.14: heard before the programme. Generated once from the script, then saved with the project."""
        if self.vlc_player and self.vlc_player.is_playing():
            self._pause_token += 1
            self.vlc_player.set_pause(1)
        if self.audio_introduction:
            self._last_spoken_text = self.audio_introduction
            self.current_desc_text_ctrl.SetValue(self.audio_introduction)
            speak_message(self.audio_introduction, interrupt=True)
            return
        if self._intro_busy:
            speak_message(_("The audio introduction is being written..."), interrupt=True)
            return
        if not self.descriptions:
            speak_message(_("No descriptions."), interrupt=True)
            return
        self._intro_busy = True
        speak_message(_("Writing the audio introduction. It will be read when ready."), interrupt=True)
        descriptions, glossary = list(self.descriptions), list(self.character_glossary)

        def work():
            try:
                text, error = audio_describer.generate_audio_introduction(
                    descriptions, glossary, os.path.splitext(os.path.basename(self.video_path or ""))[0],
                    max(0, self.vlc_player.get_length()) / 1000.0), None
            except Exception as e:
                text, error = "", e
            wx.CallAfter(done, text, error)

        def done(text, error):
            self._intro_busy = False
            if self._is_closing:
                return
            if error or not text:
                app_logger.error(f"Audio introduction failed: {error}")
                speak_message(_("Could not write the audio introduction: %s") % (error or _("empty answer")), interrupt=True)
                return
            self.audio_introduction = text
            self._dirty = True
            self.PlayAudioIntroduction()

        threading.Thread(target=work, daemon=True).start()

    def WhatsOnScreen(self):
        """One key: describe the current picture, then follow-ups in the Ask More window (it keeps the conversation)."""
        if not VLC_AVAILABLE or not self.vlc_player:
            return
        player_vqa.handle_ask_more(self)
        if self.ask_more_dialog_instance:
            self.ask_more_dialog_instance.AskNow(
                _("What is on screen right now? Describe it briefly, including any visible text."))

    def OpenEvaluation(self):
        """Audit §10.6 bis: CSA 2020 questionnaire, saved with the project."""
        from .evaluation_dialog import EvaluationDialog
        if self.vlc_player and self.vlc_player.is_playing():
            self._pause_token += 1
            self.vlc_player.set_pause(1)
        duration = (self.vlc_player.get_length() / 1000.0) if self.vlc_player and self.vlc_player.get_length() > 0 else None
        title = os.path.splitext(os.path.basename(self.video_path))[0]
        with EvaluationDialog(self, title, self.descriptions, duration, self.evaluation) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.evaluation = dlg.GetValues()
                self._dirty = True

    def AnnounceQualityReport(self):
        """Audit §9 indicators, spoken."""
        duration = (self.vlc_player.get_length() / 1000.0) if self.vlc_player and self.vlc_player.get_length() > 0 else None
        r = timing.quality_report(self.descriptions, duration)
        text = _("%(count)d descriptions. %(within).0f%% within their word budget. %(overlap)d over sound or dialogue, "
                 "%(uncertain)d uncertain, %(style)d with style issues. Average %(wps).1f words per second. Longest stretch without description: %(gap)s.") % {
            'count': r["count"], 'within': r["within_budget_pct"], 'overlap': r["overlaps_sound"],
            'uncertain': r["uncertain"], 'style': r["style"], 'wps': r["words_per_second"], 'gap': timing.format_hms(r["longest_gap"])}
        self.current_desc_text_ctrl.SetValue(text)
        speak_message(text, interrupt=True)

    def CycleADMode(self):
        """Audit §10.2: inline (ducking), extended (WCAG 1.2.7 pause) or user-driven (Describe Now)."""
        self._ad_mode = AD_MODES[(AD_MODES.index(self._ad_mode) + 1) % len(AD_MODES)]
        config_model.app_settings["player_ad_mode"] = self._ad_mode
        config_model.save_settings(config_model.app_settings)
        names = {"inline": _("Mode: descriptions in the pauses, the video keeps playing."),
                 "extended": _("Mode: extended, the video pauses when a description does not fit."),
                 "on_demand": _("Mode: on demand. Press Enter to describe the current moment, Shift+Enter for more detail.")}
        speak_message(names[self._ad_mode], interrupt=True)

    def DescribeNow(self, detailed=False):
        """On-demand mode: pause, then speak the brief (level 1) or detailed (all levels) description of now."""
        if self.vlc_player.is_playing():
            self._pause_token += 1
            self.vlc_player.set_pause(1)
        texts = timing.on_demand_texts(self.descriptions, self.vlc_player.get_time() / 1000.0, detailed)
        text = " ".join(texts) if texts else _("No description for this moment. Press Q to ask the AI.")
        self._last_spoken_text = text
        self.current_desc_text_ctrl.SetValue(text)
        self._deliver(text)

    def JumpToDescription(self, forward=True):
        if not self.vlc_player or not self._playlist:
            speak_message(_("No descriptions."), interrupt=True)
            return
        now = self.vlc_player.get_time() / 1000.0
        if forward:
            candidates = [i for i, d in enumerate(self._playlist) if d[0] > now + 0.05]
            index = candidates[0] if candidates else None
        else:
            # A small threshold so repeated presses keep moving back.
            candidates = [i for i, d in enumerate(self._playlist) if d[0] < now - 1.0]
            index = candidates[-1] if candidates else None
        if index is None:
            speak_message(_("No next description.") if forward else _("No previous description."), interrupt=True)
            return
        start = self._playlist[index][0]
        self.vlc_player.set_time(int(start * 1000))
        self._last_tick_sec = start
        text = self._playlist[index][2]
        self.currently_playing_description_index = index
        self.current_desc_text_ctrl.SetValue(text)
        self._last_spoken_text = text
        speak_message(f"{timing.format_hms(start)}. {text}", interrupt=True)

    def AnnounceTime(self):
        if not self.vlc_player: return
        speak_message(_("%(pos)s of %(total)s") % {
            'pos': timing.format_hms(self.vlc_player.get_time() / 1000.0),
            'total': timing.format_hms(max(0, self.vlc_player.get_length()) / 1000.0)}, interrupt=True)

    def ToggleAudioDescription(self):
        # EN 301 549 §7.3: AD on/off at the same level as play/pause.
        self._ad_enabled = not self._ad_enabled
        config_model.app_settings["player_ad_enabled"] = self._ad_enabled
        config_model.save_settings(config_model.app_settings)
        if not self._ad_enabled:
            self._duck_token += 1
            self._set_vlc_volume(self.volume_slider.GetValue())
        speak_message(_("Audio description on.") if self._ad_enabled else _("Audio description off."), interrupt=True)

    # --- Projects ---

    def SaveProject(self, save_as=False):
        """Save descriptions + glossary to a .omniad project. Returns True if saved."""
        path = self.project_path
        if not path or save_as:
            video_dir = os.path.dirname(self.video_path)
            default_dir = wx.StandardPaths.Get().GetDocumentsDir() if self.is_temp_video else video_dir
            default_name = os.path.splitext(os.path.basename(self.video_path))[0] + project_model.PROJECT_EXTENSION
            wildcard = _("Omni Describer projects (*%s)|*%s") % (project_model.PROJECT_EXTENSION, project_model.PROJECT_EXTENSION)
            with wx.FileDialog(self, _("Save Project"), defaultDir=default_dir, defaultFile=default_name,
                               wildcard=wildcard, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return False
                path = dlg.GetPath()
            if not path.lower().endswith(project_model.PROJECT_EXTENSION):
                path += project_model.PROJECT_EXTENSION

        video_for_project = self._project_video_path or self.video_path
        try:
            if self.is_temp_video and not self._project_video_path:
                # Downloaded videos are deleted when the player closes: keep a copy next to the project.
                import shutil
                speak_message(_("Copying video next to the project..."))
                video_for_project = os.path.splitext(path)[0] + os.path.splitext(self.video_path)[1]
                with wx.BusyCursor():
                    shutil.copy2(self.video_path, video_for_project)
            project_model.save_project(path, video_for_project, self.descriptions, self.character_glossary,
                                       self.description_token_usage, self.audio_introduction, self.evaluation)
        except OSError as e:
            app_logger.error(f"Failed to save project to {path}: {e}", exc_info=True)
            wx.MessageBox(_("Could not save the project:\n%s") % e, _("Error"), wx.OK | wx.ICON_ERROR, self)
            return False

        self.project_path = path
        self._project_video_path = video_for_project
        self._dirty = False
        try:
            config_model.remember_recent_project(path)
        except Exception as e:  # never fail a save because of the recent list
            app_logger.warning(f"Could not update recent projects: {e}")
        app_logger.info(f"Project saved: {path}")
        speak_message(_("Project saved."), interrupt=True)
        return True

    def OnCloseWindow(self, event):
        app = wx.GetApp()
        if getattr(app, 'is_updating', False):
            # Skip confirmation when the updater is closing the app
            confirmed = True
        elif self._dirty:
            answer = wx.MessageBox(_("Save this project before closing? Otherwise the generated descriptions will be lost."),
                                   _("Unsaved Descriptions"), wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
            confirmed = answer == wx.NO or (answer == wx.YES and self.SaveProject())
        else:
            confirmed = wx.MessageBox(_("Are you sure you want to close the player?"),
                                      _("Confirm Close"), wx.YES_NO | wx.ICON_QUESTION, self) == wx.YES

        if confirmed:
            if self._is_closing:
                return
            self._is_closing = True
            sound_player.stop_voice()
            if self._voice:
                self._voice.close()
            app_logger.info("PlayerWindow OnCloseWindow confirmed by user. Cleaning up.")

            if self.timer.IsRunning():
                self.timer.Stop()

            if self.vlc_events:
                try:
                    self.vlc_events.event_detach(vlc.EventType.MediaPlayerPlaying)
                    self.vlc_events.event_detach(vlc.EventType.MediaPlayerPaused)
                    self.vlc_events.event_detach(vlc.EventType.MediaPlayerEndReached)
                except Exception as e:
                    app_logger.error(f"Error detaching VLC events: {e}")
                finally:
                    self.vlc_events = None

            if self.vlc_player:
                if self.vlc_player.is_playing():
                    self.vlc_player.stop()
                self.vlc_player.release()
                self.vlc_player = None

            if self.vlc_instance:
                self.vlc_instance.release()
                self.vlc_instance = None

            if self.ask_more_dialog_instance:
                self.ask_more_dialog_instance.Destroy()
                self.ask_more_dialog_instance = None
            if self.progress_dialog:
                self.progress_dialog.Destroy()
                self.progress_dialog = None
            if self.is_temp_video and self.video_path:
                video_processor.cleanup_temp_file(self.video_path)

            # Defer the destruction until after this event handler has completed
            # to ensure a clean shutdown.
            wx.CallAfter(self.Destroy)
        else:
            if event.CanVeto():
                event.Veto()  # Prevent the window from closing
        
    @property
    def _media_setup_done(self):
        return self.vlc_player is not None

    def OnAskMore(self, event):
        player_vqa.handle_ask_more(self)
            
    def OnAskMoreDialogClosed(self, event):
        dialog = event.GetEventObject()
        if self.ask_more_dialog_instance and dialog == self.ask_more_dialog_instance:
            self.ask_more_dialog_instance = None
        if hasattr(self, '_original_state_before_ask_more') and self._original_state_before_ask_more:
            self.vlc_player.play()
        if hasattr(self, 'ask_more_button'): self.ask_more_button.SetFocus()
        event.Skip()
        
    def OnAddToMainDescription(self, event):
        text_to_add = event.GetText()
        current_time_sec = self.vlc_player.get_time() / 1000.0
        new_description = (current_time_sec, current_time_sec + 4.0, text_to_add)
        self.descriptions.append(new_description)
        self.descriptions.sort(key=lambda x: x[0])
        self._dirty = True
        self._refresh_playlist()
        self.edit_descriptions_button.Enable()
        speak_message(_("Description added to main list."))
        app_logger.info(f"Added new description at {current_time_sec:.2f}s: '{text_to_add[:50]}...'")

    def OnExploreScene(self, event):
        if not VLC_AVAILABLE: return
        player_vqa.handle_explore_scene(self)

    def OnAskMoreSubmitted(self, event):
        if not VLC_AVAILABLE: return
        player_vqa.handle_ask_more_submission(self, event)
        
    def disable_media_dependent_controls(self):
        self.play_pause_button.Disable()
        self.seek_slider.Disable()
        self.seek_back_button.Disable()
        self.seek_fwd_button.Disable()
        self.ask_more_button.Disable()
        self.explore_scene_button.Disable()
        self.edit_descriptions_button.Disable()
        self.settings_button.Disable()

    def format_time_for_display(self, millis):
        total_seconds = int(millis / 1000)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def OnExport(self, event):
        from . import player_export # Local import to prevent cycles
        player_export.start_export_process(self)

    def OnExportDone(self, event):
        if self.progress_dialog:
            self.progress_dialog.Destroy()
            self.progress_dialog = None

        if event.error:
            title = _("Export Canceled") if isinstance(event.error, InterruptedError) else _("Export Error")
            wx.MessageBox(_("Failed to export file:\n%s") % event.error, title, wx.OK | wx.ICON_ERROR, self)
        else:
            message = _("File successfully exported to:\n%s") % event.output_path
            if getattr(event, "notes", None):
                message += "\n\n" + "\n".join(event.notes)
            wx.MessageBox(message, _("Export Successful"), wx.OK | wx.ICON_INFORMATION, self)

    def OnEditDescriptionsClick(self, event):
        if not self.vlc_player or self.vlc_player.get_length() <= 0:
            wx.MessageBox(_("Please wait for the video to load before editing."), _("Info"), wx.OK | wx.ICON_INFORMATION, self)
            return

        video_duration = self.vlc_player.get_length() / 1000.0
        current_time_sec = self.vlc_player.get_time() / 1000.0

        selected_index = self.find_closest_description_index(current_time_sec)

        def _seek_video_to_sec(time_sec):
            """Seeks the VLC player to the given time in seconds."""
            if self.vlc_player:
                self.vlc_player.set_time(int(time_sec * 1000))

        with EditDescriptionDialog(self, self.descriptions, video_duration, self.character_glossary, selected_original_index=selected_index, seek_callback=_seek_video_to_sec,
                                   position_callback=lambda: max(0, self.vlc_player.get_time()) / 1000.0,
                                   play_pause_callback=lambda: self.OnPlayPause(None)) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.descriptions = dlg.GetUpdatedDescriptions()
                self.character_glossary = dlg.character_glossary
                self._dirty = True
                self._refresh_playlist()
                app_logger.info(f"Descriptions updated. New count: {len(self.descriptions)}")
                speak_message(_("Description list updated."))

                # --- FIX: Force UI refresh after editing ---
                self.currently_playing_description_index = -1
                self.OnTimer(None) # Manually trigger a UI update
                # --- END FIX ---

                if not self.descriptions:
                    self.edit_descriptions_button.Disable()
                else:
                    self.edit_descriptions_button.Enable()

    def find_closest_description_index(self, current_time_sec):
        if not self.descriptions:
            return 0
        
        # Find the description that is currently playing
        for i, (start, end, _) in enumerate(self.descriptions):
            if start <= current_time_sec < end:
                return i
        
        # If no description is currently playing, find the nearest one
        closest_index = 0
        min_distance = float('inf')
        
        for i, (start, _, _) in enumerate(self.descriptions):
            distance = abs(start - current_time_sec)
            if distance < min_distance:
                min_distance = distance
                closest_index = i
                
        return closest_index

    def OnSettings(self, event):
        with SettingsDialog(self) as dlg:
            dlg.ShowModal()

    def AppendVQAUsage(self, usage_data):
        if usage_data:
            self.vqa_token_usage.append(usage_data)
            self.update_token_usage_display()

    def update_token_usage_display(self):
        display_lines = []
        
        total_prompt = 0
        total_candidate = 0
        total_total = 0

        desc_prompt, desc_candidate, desc_total = 0, 0, 0
        glossary_prompt, glossary_candidate, glossary_total = 0, 0, 0
        has_glossary = False

        for entry in self.description_token_usage:
            if not isinstance(entry, dict): continue
            
            p = entry.get('prompt_tokens', 0) or 0
            c = entry.get('candidates_tokens', 0) or 0
            t = entry.get('total_tokens', 0) or 0

            if entry.get("type") == "glossary":
                glossary_prompt, glossary_candidate, glossary_total = p, c, t
                has_glossary = True
            else:
                desc_prompt += p
                desc_candidate += c
                desc_total += t
        
        if has_glossary:
            display_lines.append(_("--- Character Glossary ---"))
            display_lines.append(f"  {_('Prompt')}: {glossary_prompt}")
            display_lines.append(f"  {_('Output')}: {glossary_candidate}")
            display_lines.append(f"  {_('Total')}: {glossary_total}")
            total_prompt += glossary_prompt
            total_candidate += glossary_candidate
            total_total += glossary_total
        
        if desc_total > 0:
            display_lines.append(_("--- Description Generation ---"))
            display_lines.append(f"  {_('Prompt')}: {desc_prompt}")
            display_lines.append(f"  {_('Output')}: {desc_candidate}")
            display_lines.append(f"  {_('Total')}: {desc_total}")
            total_prompt += desc_prompt
            total_candidate += desc_candidate
            total_total += desc_total

        if self.vqa_token_usage:
            display_lines.append(_("--- VQA (Ask More) ---"))
            for i, entry in enumerate(self.vqa_token_usage):
                if not isinstance(entry, dict): continue
                p = entry.get('prompt_tokens', 0) or 0
                c = entry.get('candidates_tokens', 0) or 0
                t = entry.get('total_tokens', 0) or 0
                display_lines.append(f"  {_('Query %s Total')}: {t} ({_('Prompt')}: {p}, {_('Output')}: {c})")
                total_prompt += p
                total_candidate += c
                total_total += t

        if total_total > 0:
            display_lines.append("-----------------------------")
            display_lines.append(f"{_('GRAND TOTAL')}: {total_total}")
            display_lines.append(f"  ({_('Total Prompt')}: {total_prompt}, {_('Total Output')}: {total_candidate})")

        if not display_lines:
            self.token_usage_text_ctrl.SetValue(_("No AI token usage data available yet."))
        else:
            self.token_usage_text_ctrl.SetValue("\n".join(display_lines))