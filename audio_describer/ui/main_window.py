# audio_describer/ui/main_window.py
from ..i18n_setup import _
import wx
import threading # For running long tasks in the background
import os
import time # For timestamping log messages
import webbrowser # For opening help file
import sys # For getting base path for help file

from audio_describer import app_state, config
from audio_describer.ui.accessibility_utils import speak_message, set_control_accessible_name
from audio_describer.ui import file_dialogs, call_if_open
from audio_describer.core import video_processor, audio_describer, timing
from audio_describer.utils.logger import app_logger
from audio_describer.ui.player_window import PlayerWindow 
from audio_describer.ui.settings_dialog import SettingsDialog
from audio_describer.ui.manage_prompts_dialog import ManagePromptsDialog
from audio_describer.models import config_model, prompt_model
from audio_describer.utils import sound_player

# Custom event for thread completion
EVT_PROCESSING_DONE_ID = wx.NewIdRef()

class ProcessingDoneEvent(wx.PyEvent):
    """Event to signal that processing is done."""
    def __init__(self, data, error=None):
        super(ProcessingDoneEvent, self).__init__(eventType=EVT_PROCESSING_DONE_ID)
        self.data = data
        self.error = error

EVT_PROCESSING_DONE = wx.PyEventBinder(EVT_PROCESSING_DONE_ID, 1)

class MainWindow(wx.Frame):
    def __init__(self, parent, title=_("Audio Describer")):
        super(MainWindow, self).__init__(parent, title=title, size=config.DEFAULT_WINDOW_SIZE)
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.current_video_path = None
        self.is_temp_video = False
        self.prompt_data = {}
        self.last_glossary = []  # characters of the previous video, reused when the File menu toggle is on
        self.cancel_event = None

        self.create_widgets()
        self.bind_events()
        self.update_prompt_presets()

        self.panel.SetSizerAndFit(self.sizer)
        self.Layout()
        self.Centre()

    def create_widgets(self):
        full_app_display_name = config.APP_NAME_FIXED_PREFIX + config.APP_NAME_TRANSLATABLE

        # --- Menu Bar ---
        menu_bar = wx.MenuBar()
        file_menu = wx.Menu()
        self.menu_open_local = file_menu.Append(wx.ID_OPEN, _("&Open Local Video...\tCtrl+O"), _("Open a local video file"))
        self.menu_open_url = file_menu.Append(wx.ID_ANY, _("Open Video from &URL...\tCtrl+U"), _("Open a video from a direct URL"))
        self.menu_open_youtube = file_menu.Append(wx.ID_ANY, _("Open from &YouTube...\tCtrl+Y"), _("Open a video from YouTube"))
        file_menu.AppendSeparator()
        self.menu_manage_prompts = file_menu.Append(wx.ID_ANY, _("Manage &Prompt Presets..."), _("Add, edit, or delete custom prompt presets"))
        # A toggle, so it stays visibly on (screen readers say "checked") until the user turns it off.
        self.menu_reuse_characters = file_menu.AppendCheckItem(wx.ID_ANY, _("Reuse &Characters from the Previous Video"), _("Keep the same characters, names and voices for the next video, for example the next episode"))
        self.menu_reuse_characters.Enable(False)
        file_menu.AppendSeparator()
        self.menu_webapp = file_menu.Append(wx.ID_ANY, _("Open &Web Platform...\tCtrl+W"), _("Open the Omni Describer web platform in your browser"))
        file_menu.AppendSeparator()
        self.menu_settings = file_menu.Append(wx.ID_PREFERENCES, _("&Settings...\tCtrl+,"), _("Configure application settings"))
        self.menu_reset = file_menu.Append(wx.ID_ANY, _("&Reset Settings and Data..."), _("Restore default settings or presets"))
        file_menu.AppendSeparator()
        self.menu_exit = file_menu.Append(wx.ID_EXIT, _("E&xit\tAlt+F4"), _("Exit the application"))
        menu_bar.Append(file_menu, _("&File"))

        help_menu = wx.Menu()
        self.menu_help = help_menu.Append(wx.ID_HELP, _("&View Help\tF1"), _("Open the help documentation"))
        self.menu_changelog = help_menu.Append(wx.ID_ANY, _("&Changelog"), _("View the application's changelog"))
        self.menu_contributors = help_menu.Append(wx.ID_ANY, _("Co&ntributors"), _("View the list of contributors"))
        self.menu_license = help_menu.Append(wx.ID_ANY, _("View &License"), _("View the application's license agreement"))
        help_menu.AppendSeparator()
        self.menu_about = help_menu.Append(wx.ID_ABOUT, _("&About..."), _("About %s") % full_app_display_name)
        menu_bar.Append(help_menu, _("&Help"))
        self.SetMenuBar(menu_bar)

        # --- Main Content Area ---
        top_sizer = wx.BoxSizer(wx.VERTICAL)

        mode_label = wx.StaticText(self.panel, label=_("Select a video source to begin:"))
        top_sizer.Add(mode_label, 0, wx.ALL | wx.EXPAND, 5)

        self.btn_local_file = wx.Button(self.panel, label=_("&Local Video File"))
        set_control_accessible_name(self.btn_local_file, _("Process a local video file"))
        top_sizer.Add(self.btn_local_file, 0, wx.ALL | wx.EXPAND, 5)

        self.btn_direct_url = wx.Button(self.panel, label=_("Direct Video &URL"))
        set_control_accessible_name(self.btn_direct_url, _("Process a video from a direct web URL"))
        top_sizer.Add(self.btn_direct_url, 0, wx.ALL | wx.EXPAND, 5)

        self.btn_youtube_url = wx.Button(self.panel, label=_("&YouTube Video URL"))
        set_control_accessible_name(self.btn_youtube_url, _("Process a video from a YouTube URL"))
        top_sizer.Add(self.btn_youtube_url, 0, wx.ALL | wx.EXPAND, 5)

        top_sizer.AddSpacer(5)
        webapp_label = wx.StaticText(self.panel, label=_("Or try the web platform (no API key needed):"))
        top_sizer.Add(webapp_label, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
        self.btn_webapp = wx.Button(self.panel, label=_("Open &Web Platform"))
        set_control_accessible_name(self.btn_webapp, _("Open the Omni Describer web platform in your browser"))
        top_sizer.Add(self.btn_webapp, 0, wx.ALL | wx.EXPAND, 5)

        prompt_label = wx.StaticText(self.panel, label=_("Optional: Select a prompt preset to enhance generation:"))
        top_sizer.Add(prompt_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 5)
        
        self.prompt_choice = wx.Choice(self.panel, name="PromptPresetChoice")
        set_control_accessible_name(self.prompt_choice, _("Select an optional prompt preset."))
        top_sizer.Add(self.prompt_choice, 0, wx.ALL | wx.EXPAND, 5)
        
        self.prompt_display_text = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 80))
        set_control_accessible_name(self.prompt_display_text, _("The full text of the selected prompt."))
        top_sizer.Add(self.prompt_display_text, 0, wx.ALL | wx.EXPAND, 5)

        # --- Add top sizer to the main sizer ---
        self.sizer.Add(top_sizer, 0, wx.EXPAND)

        # --- Status Log ---
        status_box = wx.StaticBox(self.panel, label=_("Status Log"))
        status_box_sizer = wx.StaticBoxSizer(status_box, wx.VERTICAL)
        self.log_text_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.VSCROLL, size=(-1, 150))
        set_control_accessible_name(self.log_text_ctrl, _("Application status and processing messages."))
        status_box_sizer.Add(self.log_text_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        self.sizer.Add(status_box_sizer, 1, wx.EXPAND | wx.ALL, 5)

        # --- Bottom Button Bar ---
        bottom_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.settings_button = wx.Button(self.panel, label=_("&Settings..."))
        set_control_accessible_name(self.settings_button, _("Open application settings"))
        bottom_button_sizer.Add(self.settings_button, 0, wx.ALL, 5)
        self.cancel_button = wx.Button(self.panel, label=_("&Cancel Generation"))
        set_control_accessible_name(self.cancel_button, _("Cancel the running generation (Escape)"))
        self.cancel_button.Disable()
        bottom_button_sizer.Add(self.cancel_button, 0, wx.ALL, 5)
        bottom_button_sizer.AddStretchSpacer(1) # Pushes the exit button to the right
        self.exit_button = wx.Button(self.panel, id=wx.ID_EXIT, label=_("E&xit"))
        bottom_button_sizer.Add(self.exit_button, 0, wx.ALL, 5)
        
        self.sizer.Add(bottom_button_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.statusBar = self.CreateStatusBar(2)
        self.statusBar.SetStatusWidths([-3, -1])
        self.update_status_bar(_("Ready"), speak=False)

    def bind_events(self):
        self.Bind(wx.EVT_MENU, self.on_open_local_file, self.menu_open_local)
        self.Bind(wx.EVT_MENU, self.on_open_url, self.menu_open_url)
        self.Bind(wx.EVT_MENU, self.on_open_youtube, self.menu_open_youtube)
        self.Bind(wx.EVT_MENU, self.on_manage_prompts, self.menu_manage_prompts)
        self.Bind(wx.EVT_MENU, self.on_open_webapp, self.menu_webapp)
        self.Bind(wx.EVT_MENU, self.on_settings, self.menu_settings)
        self.Bind(wx.EVT_MENU, self.on_reset_data, self.menu_reset)
        self.Bind(wx.EVT_MENU, self.on_exit, self.menu_exit)
        self.Bind(wx.EVT_MENU, self.on_help, self.menu_help)
        self.Bind(wx.EVT_MENU, self.on_open_changelog, self.menu_changelog)
        self.Bind(wx.EVT_MENU, self.on_open_contributors, self.menu_contributors)
        self.Bind(wx.EVT_MENU, self.on_open_license, self.menu_license)
        self.Bind(wx.EVT_MENU, self.on_about, self.menu_about)
        self.btn_local_file.Bind(wx.EVT_BUTTON, self.on_open_local_file)
        self.btn_direct_url.Bind(wx.EVT_BUTTON, self.on_open_url)
        self.btn_youtube_url.Bind(wx.EVT_BUTTON, self.on_open_youtube)
        self.btn_webapp.Bind(wx.EVT_BUTTON, self.on_open_webapp)
        self.prompt_choice.Bind(wx.EVT_CHOICE, self.on_prompt_selected)
        self.settings_button.Bind(wx.EVT_BUTTON, self.on_settings)
        self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel_generation)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        self.exit_button.Bind(wx.EVT_BUTTON, self.on_exit)
        self.Bind(wx.EVT_CLOSE, self.on_close_window)
        self.Bind(EVT_PROCESSING_DONE, self.on_processing_complete)

    def set_processing_status(self, message, sound_file=None):
        self.update_status_bar(message)
        self.log_to_ui(message)
        if sound_file:
            sound_player.play(sound_file)

    def log_to_ui(self, message):
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        log_message = f"[{timestamp}] {message}\n"
        wx.CallAfter(self.log_text_ctrl.AppendText, log_message)
        app_logger.info(f"UI_LOG: {message}")

    def update_prompt_presets(self):
        current_lang = config_model.get_setting("application_language")
        prompts = prompt_model.get_prompts_for_language(current_lang)
        self.prompt_choice.Clear()
        self.prompt_data.clear()
        none_option_text = _("(No Preset Selected)")
        self.prompt_choice.Append(none_option_text)
        if not prompts:
            self.prompt_choice.SetSelection(0)
            self.prompt_display_text.SetValue("")
            self.prompt_display_text.Enable()  # Keep enabled for custom prompts
            return
        for prompt in prompts:
            self.prompt_choice.Append(prompt["name"])
            self.prompt_data[prompt["name"]] = prompt["prompt"]
        self.prompt_choice.SetSelection(0)
        self.on_prompt_selected(None)

    def on_prompt_selected(self, event):
        selected_name = self.prompt_choice.GetStringSelection()
        if selected_name in self.prompt_data:
            self.prompt_display_text.SetValue(self.prompt_data[selected_name])
            self.prompt_display_text.Enable()
        else:
            self.prompt_display_text.SetValue("")
            self.prompt_display_text.Enable()  # Keep enabled so users can enter custom text
        if event: event.Skip()

    def on_manage_prompts(self, event):
        with ManagePromptsDialog(self) as dlg:
            dlg.ShowModal()
        self.update_prompt_presets()

    def on_reset_data(self, event):
        from audio_describer.ui.reset_dialog import ResetDialog
        with ResetDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
        audio_describer.reset_gemini_client()  # keys may have changed
        self.update_prompt_presets()
        self.set_processing_status(_("Reset done. Some settings apply after restarting the app."))

    def update_status_bar(self, message, speak=True, field=0):
        self.statusBar.SetStatusText(message, field)
        if field == 0 and speak: speak_message(message)

    def update_progress_bar(self, value, field=1):
        if value == -1: self.statusBar.SetStatusText("", field)
        elif value == 0: self.statusBar.SetStatusText(_("Processing..."), field)
        elif 0 < value <= 100: self.statusBar.SetStatusText(_("%s%%") % value, field)

    def on_open_local_file(self, event):
        file_path = file_dialogs.show_open_video_dialog(self, config.SUPPORTED_VIDEO_FORMATS)
        if file_path:
            self.start_processing_thread(video_processor.process_local_file, (file_path,), self.prompt_display_text.GetValue(), _("Preparing local file..."))

    def on_open_url(self, event):
        video_url = file_dialogs.show_url_input_dialog(
            self,
            _("Enter Direct Video URL"),
            _("Enter direct URL to video file (e.g., .mp4, .webm):"),
            is_youtube=False
        )
        if video_url:
            self.start_processing_thread(video_processor.process_direct_url, (video_url,), self.prompt_display_text.GetValue(), _("Downloading from URL..."))

    def on_open_youtube(self, event):
        youtube_url = file_dialogs.show_url_input_dialog(
            self,
            _("Enter YouTube Video URL"),
            _("Enter a valid YouTube video URL:"),
            is_youtube=True
        )
        if youtube_url:
            desired_resolution = config_model.get_setting("youtube_download_quality")
            self.start_processing_thread(video_processor.process_youtube_url, (youtube_url, desired_resolution), self.prompt_display_text.GetValue(), _("Downloading from YouTube..."))

    def on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE and self.cancel_button.IsEnabled():
            self.on_cancel_generation(None)
            return
        event.Skip()

    def on_cancel_generation(self, event):
        if self.cancel_event is not None and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.log_text_ctrl.SetFocus()  # a disabled button drops focus, leaving the screen reader nowhere
            self.cancel_button.Disable()
            self.set_processing_status(_("Cancelling..."))

    def start_processing_thread(self, target_function, target_args, user_prompt, initial_status_msg):
        self.cancel_event = threading.Event()
        self.log_text_ctrl.Clear()
        self.set_processing_status(initial_status_msg, sound_file="aistart.mp3")
        self.update_progress_bar(0)
        self.toggle_ui_elements(enable=False)

        reuse = self.last_glossary if self.menu_reuse_characters.IsChecked() else None
        thread = threading.Thread(target=self._processing_task_runner,
                                  args=(target_function, target_args, user_prompt, self.cancel_event, reuse))
        thread.daemon = True
        thread.start()

    def _processing_task_runner(self, video_handler_func, video_handler_args, user_prompt, cancel_event, initial_glossary=None):
        # Escape on this run: ffmpeg, Gemini requests, retry waits and the upload below stop early.
        app_state.set_cancel_event(cancel_event)
        def ui_status_updater(message):
            if "Video processing" in message and "state:" in message:
                sound_player.play("progress.mp3")
            
            user_friendly_messages = [
                _("Video is ACTIVE."), _("AI is thinking..."),
                _("Successfully parsed"), _("Timestamp correction complete"),
                _("Removed repetitive descriptions")
            ]
            if any(msg in message for msg in user_friendly_messages):
                 wx.CallAfter(self.set_processing_status, message)

        original_video_path = None
        processed_for_ai_path = None
        was_preprocessed = False
        all_descriptions = []
        character_glossary = []
        all_descriptions_token_usage = []

        try:
            original_video_path, is_temp = video_handler_func(*video_handler_args)
            self.is_temp_video = is_temp
            self.current_video_path = original_video_path  # so a cancelled download is cleaned up too
            app_state.raise_if_cancelled()

            if not original_video_path or not os.path.exists(original_video_path):
                raise Exception(_("Video processing or downloading failed."))

            # --- START: SECURITY FIX ---
            # Generate the pre-processed video for AI analysis first.
            processed_for_ai_path, was_preprocessed = video_processor.preprocess_video_for_ai(
                original_video_path, 
                lambda msg: wx.CallAfter(self.set_processing_status, msg)
            )
            # Determine which video file to use for all subsequent AI tasks.
            # If pre-processing was successful, use the smaller file. Otherwise, use the original.
            video_for_ai = processed_for_ai_path if was_preprocessed else original_video_path
            # --- END: SECURITY FIX ---
            app_state.raise_if_cancelled()

            enable_chunking = config_model.get_setting("enable_video_chunking")
            enable_glossary = config_model.get_setting("enable_character_glossary")

            if enable_glossary:
                status_message = _("Asking AI to generate descriptions and glossary...")
            else:
                status_message = _("Asking AI to generate descriptions...")

            if enable_chunking:
                chunk_duration = config_model.get_setting("video_chunk_duration_seconds")
                wx.CallAfter(self.set_processing_status, _("Processing video..."), sound_file="going.mp3")
                all_descriptions, character_glossary, all_descriptions_token_usage = audio_describer.generate_descriptions_chunked(
                    video_for_ai, chunk_duration, user_prompt, ui_status_updater, initial_glossary=initial_glossary,
                    audio_source_path=original_video_path  # the AI copy may have no sound
                )
            else:
                wx.CallAfter(self.set_processing_status, status_message, sound_file="going.mp3")
                all_descriptions, character_glossary, main_usage = audio_describer.generate_descriptions_and_glossary(
                    video_for_ai, user_prompt, ui_status_updater, initial_glossary=initial_glossary,
                    audio_source_path=original_video_path)  # the AI copy may have no sound
                if main_usage:
                    all_descriptions_token_usage.append(main_usage)

            if all_descriptions is None:
                raise audio_describer.GeminiAPIError(_("AI processing failed to provide descriptions for an unknown reason."))

            wx.CallAfter(self.set_processing_status, _("Finalizing descriptions..."))
            final_descriptions = audio_describer._remove_consecutive_duplicates(all_descriptions, ui_status_updater)
            final_descriptions, _cast = timing.apply_voice_casting(final_descriptions, character_glossary)
            
            # The token usage is now unified, so we just pass it along.
            # We can add a type if we want to distinguish it in the future.
            # For now, we just pass the list of usage dicts.
            
            call_if_open(self, wx.PostEvent, self, ProcessingDoneEvent(data=(original_video_path, final_descriptions, all_descriptions_token_usage, character_glossary)))

        except Exception as e:
            error = e
            if app_state.cancel_requested() and not isinstance(e, audio_describer.GenerationCancelled):
                # A step stopped by Escape (e.g. a killed ffmpeg) may fail with its own error: report the cancel.
                app_logger.info(f"Error after cancellation, reported as cancelled: {e}")
                error = audio_describer.GenerationCancelled("Generation cancelled by user.")
            call_if_open(self, wx.PostEvent, self, ProcessingDoneEvent(data=None, error=error))
        finally:
            app_state.set_cancel_event(None)
            if was_preprocessed and processed_for_ai_path and os.path.exists(processed_for_ai_path):
                video_processor.cleanup_temp_file(processed_for_ai_path)

    def on_processing_complete(self, event):
        self.update_progress_bar(-1)
        self.toggle_ui_elements(enable=True)

        if event.error:
            from audio_describer.core.audio_describer import ContentBlockedError, TokenLimitError, GenerationCancelled
            cancelled = isinstance(event.error, GenerationCancelled)
            sound_player.play("aistop.mp3" if cancelled else "error.mp3")
            if cancelled:  # the user asked for it: no error dialog
                self.set_processing_status(_("Generation cancelled."))
            elif isinstance(event.error, ContentBlockedError):
                reason = event.error.reason or _("Unknown")
                title = _("Content Generation Blocked")
                message = _(
                    "The AI was unable to generate descriptions because its content safety filters were triggered.\n\n"
                    "Reason: %s\n\n"
                    "This can sometimes happen with content that is violent, sensitive, or complex. Here are some things you can try:\n\n"
                    "1. Go to Settings -> AI Settings and enable 'Disable Safety Filters'. (Use with caution)\n"
                    "2. Reduce the 'Frame Rate for AI Analysis' in settings to send less data to the AI.\n"
                    "3. Try a different video if the issue persists."
                ) % reason
                self.set_processing_status(f"{title} (Reason: {reason})")
                wx.MessageBox(message, title, wx.OK | wx.ICON_WARNING, self)
            
            elif isinstance(event.error, TokenLimitError):
                title = _("Processing Limit Reached")
                message = _(
                    "The AI stopped because the video is too long or complex to process in a single pass.\n\n"
                    "This is a capacity limit, not a content safety issue. Here are the best ways to solve this:\n\n"
                    "1. (Recommended) Go to Settings -> AI Settings and enable 'Video Chunking'. This will automatically split the video into manageable parts for the AI.\n"
                    "2. Reduce the 'Frame Rate for AI Analysis' in settings to make the video simpler for the AI.\n"
                    "3. If you are an advanced user, you can try specifying a more powerful model in the 'Gemini Model Override' setting."
                )
                self.set_processing_status(title)
                wx.MessageBox(message, title, wx.OK | wx.ICON_WARNING, self)

            else:
                error_message = _("An error occurred during processing:\n%s") % str(event.error)
                self.set_processing_status(error_message)
                wx.MessageBox(error_message, _("Processing Error"), wx.OK | wx.ICON_ERROR, self)

            if self.is_temp_video and self.current_video_path:
                 video_processor.cleanup_temp_file(self.current_video_path)
                 self.current_video_path = None
            return

        video_path, descriptions, token_usage, character_glossary = event.data
        if character_glossary:  # the same list the player edits, so voices chosen there are reused too
            self.last_glossary = character_glossary
            self.menu_reuse_characters.Enable(True)
        if descriptions:
            sound_player.play("finish.mp3")
            success_msg = _("Success! Generated %d audio descriptions.") % len(descriptions)
            if character_glossary:
                success_msg += " " + _("Identified %d characters.") % len(character_glossary)
            self.set_processing_status(success_msg)
            
            try:
                # Pass the glossary to the player window
                player_dlg = PlayerWindow(self, video_path, descriptions, self.is_temp_video, token_usage, character_glossary)
                player_dlg.ShowModal()
            except Exception as e:
                app_logger.error("Error opening video player: %s", e, exc_info=True)
                self.set_processing_status(_("Error opening video player: %s") % e)
                if self.is_temp_video and self.current_video_path:
                    video_processor.cleanup_temp_file(self.current_video_path)
            
            self.set_processing_status(_("Ready for next video."))
        else:
            sound_player.play("error.mp3")
            no_desc_msg = _("Processing complete, but the AI did not generate any descriptions for this video.")
            self.set_processing_status(no_desc_msg)
            wx.MessageBox(no_desc_msg, _("Processing Note"), wx.OK | wx.ICON_INFORMATION, self)
            if self.is_temp_video and self.current_video_path:
                 video_processor.cleanup_temp_file(self.current_video_path)
                 self.current_video_path = None

    def toggle_ui_elements(self, enable=True):
        self.btn_local_file.Enable(enable)
        self.btn_direct_url.Enable(enable)
        self.btn_youtube_url.Enable(enable)
        self.prompt_choice.Enable(enable)
        self.settings_button.Enable(enable)
        if enable and self.FindFocus() in (None, self.cancel_button):
            self.btn_local_file.SetFocus()  # don't leave the screen reader on a disabled button
        self.cancel_button.Enable(not enable)
        if not enable:
            self.cancel_button.SetFocus()
        if self.GetMenuBar():
            self.GetMenuBar().EnableTop(0, enable)

    def on_settings(self, event):
        with SettingsDialog(self) as settings_dlg:
            ret_code = settings_dlg.ShowModal()
        if ret_code == wx.ID_OK:
            self.update_prompt_presets()

    def _open_doc_file(self, filename):
        try:
            doc_dir = config.get_doc_dir()
            doc_dir = os.path.normpath(doc_dir)
            lang_code = config_model.get_setting('application_language') or 'en'
            
            lang_specific_file = os.path.join(doc_dir, lang_code, filename)
            default_file = os.path.join(doc_dir, 'en', filename)
            
            file_to_open = lang_specific_file if os.path.exists(lang_specific_file) else default_file
            
            if os.path.exists(file_to_open):
                webbrowser.open(f"file:///{os.path.realpath(file_to_open)}")
                self.log_to_ui(f"Opening doc file: {file_to_open}")
            else:
                self.log_to_ui(f"Doc file not found. Checked: {file_to_open}")
                app_logger.warning(f"User attempted to open missing document: {file_to_open}")

        except Exception as e:
            self.log_to_ui(f"Error opening doc file '{filename}': {e}")
            app_logger.error(f"Failed to open document '{filename}': {e}", exc_info=True)

    def on_open_webapp(self, event):
        try:
            webbrowser.open(config.WEBAPP_URL)
        except Exception as e:
            app_logger.error(f"Failed to open webapp URL: {e}", exc_info=True)

    def on_help(self, event): self._open_doc_file("help.html")
    def on_open_changelog(self, event): self._open_doc_file("changes.txt")
    def on_open_contributors(self, event): self._open_doc_file("contributors.txt")

    def on_open_license(self, event):
        from .license_dialog import LicenseDialog
        with LicenseDialog(self, view_only=True) as dlg:
            dlg.ShowModal()

    def on_about(self, event):
        with AboutDialog(self) as dlg:
            dlg.ShowModal()

    def on_exit(self, event):
        self.Close(True)

    def on_close_window(self, event):
        self._is_closing = True  # late worker callbacks (call_if_open) are dropped from now on
        if self.cancel_event:  # stop a running generation (ffmpeg, HTTP, retries) before tearing down
            self.cancel_event.set()
        if self.is_temp_video and self.current_video_path and os.path.exists(self.current_video_path):
            video_processor.cleanup_temp_file(self.current_video_path)
        self.Destroy()

class AboutDialog(wx.Dialog):
    def __init__(self, parent):
        full_app_display_name = config.APP_NAME_FIXED_PREFIX + config.APP_NAME_TRANSLATABLE
        super().__init__(parent, title=_("About %s") % full_app_display_name)

        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        app_name_text = wx.StaticText(panel, label=f"{full_app_display_name} v{config.APP_VERSION}")
        font = app_name_text.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        app_name_text.SetFont(font)
        panel_sizer.Add(app_name_text, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)

        desc_text = wx.StaticText(panel, label=_("An application to automatically generate audio descriptions for videos using Gemini AI.\n\nDeveloped with accessibility in mind."))
        panel_sizer.Add(desc_text, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)
        
        contact_website_sizer = wx.BoxSizer(wx.HORIZONTAL)
        contact_label = wx.StaticText(panel, label=_("Contact:"))
        contact_website_sizer.Add(contact_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        contact_email_text = wx.StaticText(panel, label=config.CONTACT_EMAIL)
        contact_website_sizer.Add(contact_email_text, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        contact_website_sizer.AddStretchSpacer(1)
        self.website_button = wx.Button(panel, label=_("Visit Website"))
        contact_website_sizer.Add(self.website_button, 0, wx.ALIGN_CENTER_VERTICAL, 0)
        panel_sizer.Add(contact_website_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        panel.SetSizer(panel_sizer)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        
        button_sizer = self.CreateStdDialogButtonSizer(wx.OK)
        ok_button = self.FindWindowById(wx.ID_OK)
        if ok_button: ok_button.SetLabel(_("Close"))
        dialog_sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        self.SetSizerAndFit(dialog_sizer)
        self.CentreOnParent()
        self.website_button.Bind(wx.EVT_BUTTON, self.on_visit_website)

    def on_visit_website(self, event):
        try:
            webbrowser.open(config.WEBSITE_URL)
        except Exception as e:
            app_logger.error(f"Failed to open website URL '{config.WEBSITE_URL}': {e}", exc_info=True)