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
from ..core import video_processor
from .accessibility_utils import speak_message, set_control_accessible_name
from .edit_description_dialog import EditDescriptionDialog
from .glossary_dialog import GlossaryDialog
from .ask_more_dialog import AskMoreSubmitEvent, EVT_ASK_MORE_SUBMIT, AskMoreAddToMainEvent, EVT_ASK_MORE_ADD_TO_MAIN
from . import player_export
from . import player_vqa
from .settings_dialog import SettingsDialog
from ..models import config_model

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

class PlayerWindow(wx.Dialog):
    def __init__(self, parent, video_path, descriptions, is_temp_video=False, description_token_usage=None, character_glossary=None, title=_("Video Player with Descriptions")):
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
        self.glossary_token_usage = {}
        
        self.vlc_instance = None
        self.vlc_player = None
        self.vlc_events = None
        if not VLC_AVAILABLE:
            wx.MessageBox(_("VLC library is not installed or could not be loaded. Playback will be unavailable."), "Error", wx.OK | wx.ICON_ERROR)
        
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
        # Voice casting: which voice reads each character's translated dialogue (works without VLC).
        self.character_voices_button = wx.Button(self.panel, label=_("Character &Voices..."))
        self.character_voices_button.Bind(wx.EVT_BUTTON, self.OnCharacterVoices)
        self.character_voices_button.Enable(bool(self.character_glossary))
        action_buttons_sizer.Add(self.character_voices_button, 0, wx.ALL, 5)

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
        
        self.Bind(EVT_ASK_MORE_SUBMIT, self.OnAskMoreSubmitted)
        self.Bind(EVT_ASK_MORE_ADD_TO_MAIN, self.OnAddToMainDescription)
        
    def OnPlayPause(self, event):
        if not self.vlc_player or self._is_closing:
            return

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
        self.vlc_player.audio_set_volume(new_volume)
        config_model.app_settings["player_volume_percent"] = new_volume
        config_model.save_settings(config_model.app_settings)

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
            found_desc = False
            
            # Check for active description
            for i, (start, end, text) in enumerate(self.descriptions):
                if start <= current_pos_sec < end:
                    # If we found a description that should be active...
                    if i != self.currently_playing_description_index:
                        self.currently_playing_description_index = i
                        self.current_desc_text_ctrl.SetValue(text)
                        
                        # Only speak if we are actually playing, otherwise just show text
                        if self.vlc_player.is_playing():
                            interrupt_speech = config_model.get_setting("player_allow_speech_interruption")
                            speak_message(text, interrupt=interrupt_speech)
                    
                    found_desc = True
                    break
            
            if not found_desc:
                if self.currently_playing_description_index != -1:
                    self.currently_playing_description_index = -1
                    self.current_desc_text_ctrl.SetValue("")

    def OnCloseWindow(self, event):
        app = wx.GetApp()
        if getattr(app, 'is_updating', False):
            # Skip confirmation when the updater is closing the app
            result = 2
        else:
            result = wx.MessageBox(_("Are you sure you want to close the player?"),
                                   _("Confirm Close"),
                                   wx.YES_NO | wx.ICON_QUESTION)
            app_logger.info(f"wx.MessageBox returned: {result}") # Debug log

        if result == 2: # 2 is the actual return value for Yes in this environment
            if self._is_closing:
                return
            self._is_closing = True
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
            if self.progress_dialog:
                self.progress_dialog.Destroy()
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
        self.edit_descriptions_button.Enable()
        speak_message(_("Description added to main list."))
        app_logger.info(f"Added new description at {current_time_sec:.2f}s: '{text_to_add[:50]}...'")

    def OnExploreScene(self, event):
        if not VLC_AVAILABLE: return
        player_vqa.handle_explore_scene(self)

    def OnAskMoreSubmitted(self, event):
        if not VLC_AVAILABLE: return
        player_vqa.handle_ask_more_submission(self, event)
        
    def OnCharacterVoices(self, event):
        with GlossaryDialog(self, self.character_glossary, self.descriptions) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                # In place: the main window reuses this glossary (and its voices) for the next video.
                self.character_glossary[:] = dlg.glossary
                self.descriptions = dlg.descriptions
                speak_message(_("Character voices updated."))

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
            wx.MessageBox(_("File successfully exported to:\n%s") % event.output_path, _("Export Successful"), wx.OK | wx.ICON_INFORMATION, self)

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

        with EditDescriptionDialog(self, self.descriptions, video_duration, self.character_glossary, selected_original_index=selected_index, seek_callback=_seek_video_to_sec) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.descriptions = dlg.GetUpdatedDescriptions()
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