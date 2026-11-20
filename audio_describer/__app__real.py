# audio_describer/__app__real.py
import wx
import sys
import os
import time
import threading # Import threading for the background check

# --- Top-level imports should be minimal ---
import audio_describer.i18n_setup 
from audio_describer.utils.logger import app_logger
from audio_describer.utils import sound_player
from audio_describer import app_state

class AudioDescriberApp(wx.App):
    def OnInit(self):
        self.is_updating = False
        
        self.lang_code = audio_describer.i18n_setup.initialize_translations()
        
        from audio_describer.utils import logger
        logger.update_log_level()
        
        _ = audio_describer.i18n_setup._

        from audio_describer.models import prompt_model, config_model

        # Ensure settings are loaded safely - never let this crash the app
        try:
            initial_settings = config_model.load_settings()
            if initial_settings:
                config_model.app_settings = initial_settings
            else:
                config_model.app_settings = config_model.DEFAULT_SETTINGS.copy()
                app_logger.warning("Settings loaded as None, using defaults.")
            app_logger.info("Settings loaded successfully.")
        except Exception as e:
            app_logger.error(f"Failed to load settings: {e}. Using defaults.", exc_info=True)
            config_model.app_settings = config_model.DEFAULT_SETTINGS.copy()
        
        prompt_model.load_prompts()
        app_logger.info("Prompt model initialized.")

        from audio_describer.ui.license_dialog import LicenseDialog

        try:
            license_accepted = config_model.get_setting("license_accepted")
        except Exception:
            license_accepted = False
            app_logger.error("Failed to check license acceptance status.")
        
        if not license_accepted:
            license_dlg = LicenseDialog(None)
            result = license_dlg.ShowModal()
            license_dlg.Destroy()

            if result == wx.ID_OK:
                app_logger.info("License accepted by user.")
                config_model.app_settings["license_accepted"] = True
                config_model.save_settings(config_model.app_settings)
            else:
                app_logger.warning("License was not accepted. Exiting application.")
                return False
        
        from audio_describer.ui.main_window import MainWindow 
        import audio_describer.config as app_config
        from audio_describer.ui.accessibility_utils import speak_message, ACCESSIBLE_OUTPUT_AVAILABLE
        
        app_logger.info("Core components loaded. Initializing main window.")

        # --- The update checker call is now moved from here ---
        
        translatable_part = _(app_config.APP_NAME_TRANSLATABLE)
        full_app_display_name = app_config.APP_NAME_FIXED_PREFIX + translatable_part

        app_logger.info(_("Starting %(app_name)s v%(app_version)s") % {
            "app_name": full_app_display_name, 
            "app_version": app_config.APP_VERSION
        })
        
        if ACCESSIBLE_OUTPUT_AVAILABLE: app_logger.info("accessible-output2 initialized.")
        else: app_logger.warning("accessible-output2 not available.")
        
        window_title = full_app_display_name + f" - v{app_config.APP_VERSION}" 

        self.main_frame = MainWindow(None, title=window_title)
        self.SetTopWindow(self.main_frame)
        self.main_frame.Show(True)

        # Show settings migration warning if there was an issue
        load_warning = config_model.get_load_warning()
        if load_warning:
            app_logger.warning(f"Settings load warning: {load_warning}")
            wx.MessageBox(
                load_warning,
                _("Settings Notice"),
                wx.OK | wx.ICON_WARNING,
                self.main_frame
            )

        # Show one-time webapp migration banner
        self._show_webapp_banner_if_needed(config_model)

        # MODIFICATION: Start the update check in the background AFTER the UI is shown.
        self.StartBackgroundUpdateCheck()

        sound_player.play("start.mp3")

        speak_message(_("%(app_name)s started.") % {
            "app_name": full_app_display_name
        }, interrupt=True)

        return True

    def _show_webapp_banner_if_needed(self, config_model):
        """Shows a one-time banner informing users about the web platform."""
        import audio_describer.config as app_config
        _ = audio_describer.i18n_setup._

        try:
            already_shown = config_model.get_setting("webapp_banner_shown")
        except Exception:
            already_shown = False

        if already_shown:
            return

        message = _(
            "Omni Describer is now available as a web platform!\n\n"
            "The desktop app will continue to work, but new features and improvements "
            "are now focused on the web version at:\n\n"
            "%(webapp_url)s\n\n"
            "The web platform requires no installation, no API keys, and works on "
            "all devices including Mac and mobile.\n\n"
            "Would you like to open the web platform now?\n\n"
            "(This message will only be shown once.)"
        ) % {"webapp_url": app_config.WEBAPP_URL}

        result = wx.MessageBox(
            message,
            _("Omni Describer Web Platform"),
            wx.YES_NO | wx.ICON_INFORMATION,
            self.main_frame
        )

        if result == wx.YES:
            import webbrowser
            webbrowser.open(app_config.WEBAPP_URL)

        # Mark as shown regardless of choice
        config_model.app_settings["webapp_banner_shown"] = True
        config_model.save_settings(config_model.app_settings)

    # NEW METHOD: To handle the background thread for the update check.
    def StartBackgroundUpdateCheck(self):
        """
        Launches the update check in a separate thread to avoid blocking the UI.
        """
        from audio_describer.utils import update_checker
        import audio_describer.config as app_config
        _ = audio_describer.i18n_setup._
        if getattr(sys, "frozen", False):
            update_checker.install_pending_updater()
            # An updater from a flat (PyInstaller 5) release has no cleanup step, so the
            # first PyInstaller 6 run removes the old layout's leftovers itself.
            if os.path.basename(getattr(sys, "_MEIPASS", "")) == "_internal":
                from audio_describer.utils.layout_cleanup import remove_stale_flat_layout
                remove_stale_flat_layout(os.path.dirname(sys.executable), app_logger)

        thread = threading.Thread(
            target=update_checker.check_for_updates,
            args=(
                self.main_frame, # Pass the main window reference
                app_config.APP_VERSION,
                app_config.VERSION_URL,
                app_config.DOWNLOAD_URL,
                self.lang_code,
                _,
            ),
            daemon=True # Daemon threads exit when the main app exits
        )
        thread.start()

    def OnExit(self):
        if getattr(self, 'is_updating', False):
            app_logger.info("Performing fast exit for update process. No sounds or delays.")
            app_state.shutdown_event.set()
            return super().OnExit()

        app_logger.info("Shutdown initiated. Setting global shutdown event.")
        app_state.shutdown_event.set()
        
        _ = audio_describer.i18n_setup._
        import audio_describer.config as app_config
        
        translatable_part = _(app_config.APP_NAME_TRANSLATABLE)
        full_app_display_name = app_config.APP_NAME_FIXED_PREFIX + translatable_part
        
        app_logger.info(_("%s is shutting down.") % full_app_display_name)
        
        from audio_describer.ui.accessibility_utils import speak_message
        speak_message(_("%(app_name)s closed.") % {
            "app_name": full_app_display_name
        })
        
        sound_player.play("shutdown.mp3")
        time.sleep(0.75) 
        
        return super().OnExit()

def main():
    app = AudioDescriberApp(redirect=False)
    app.MainLoop()

if __name__ == '__main__':
    main()