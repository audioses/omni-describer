# audio_describer/core/youtube_downloader.py
from audio_describer.i18n_setup import _ # Assuming this is correctly imported elsewhere or handled
import os
import tempfile
import subprocess
import json
import sys # <-- Import sys to check for frozen state

from audio_describer import config # For TEMP_DIR_NAME
from audio_describer.utils.logger import app_logger
from audio_describer.models import config_model # For getting settings
# Re-import _ if it was missing, or ensure it's globally available if using a framework
try:
    from audio_describer.i18n_setup import _
except ImportError:
    # Define a dummy _ for standalone testing if i18n_setup isn't present
    def _(text):
        return text
    app_logger.warning("Could not import i18n_setup. Using dummy translation function.") # Keep unwrapped

# Define a directory for temporary downloads
# Using a subfolder within the main application's temporary directory
TEMP_DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "OmniDescriber", config.TEMP_DIR_NAME, "downloads")
if not os.path.exists(TEMP_DOWNLOAD_DIR):
    try:
        os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)
    except Exception as e:
        # Keep this error unwrapped
        app_logger.error("Could not create temp download directory %s: %s" % (TEMP_DOWNLOAD_DIR, e))
        # Fallback if creation fails, though this is not ideal
        TEMP_DOWNLOAD_DIR = tempfile.mkdtemp(prefix="omni_downloads_")
        if not os.path.exists(TEMP_DOWNLOAD_DIR):
             # Added a try/except around the fallback creation too
             try:
                 os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)
             except Exception as e_fallback:
                  app_logger.error("Could not create fallback temp download directory %s: %s" % (TEMP_DOWNLOAD_DIR, e_fallback)) # Keep unwrapped
                  # At this point, temp directory creation failed completely.
                  # This will likely cause issues later, but the code can proceed
                  # and fail more gracefully during download attempt.


class DownloaderError(Exception):
    """Custom exception for downloader errors."""
    pass


# --- Logic to find the yt-dlp executable ---
# This block runs once when the module is imported

# Check if the script is running as a bundled executable (frozen)
IS_FROZEN = getattr(sys, 'frozen', False)

YT_DLP_COMMAND = 'yt-dlp' # Default command, assumes it's in system PATH

if IS_FROZEN:
    # Running in a frozen environment (e.g., PyInstaller)
    # Determine the base path based on one-file vs one-folder mode
    base_path = None
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller one-file mode: files are unpacked to a temporary directory
        base_path = sys._MEIPASS
        app_logger.debug("Running in PyInstaller one-file mode. Base path: %s" % base_path) # Keep unwrapped
    else:
        # PyInstaller one-folder mode: files are relative to the executable's directory
        base_path = os.path.dirname(sys.executable)
        app_logger.debug("Running in PyInstaller one-folder mode. Base path: %s" % base_path) # Keep unwrapped

    if base_path:
        # Construct the expected path to yt-dlp.exe relative to the base path
        # Assumes yt-dlp.exe is bundled into a 'bin' subfolder
        expected_bundled_path = os.path.join(base_path, 'bin', 'yt-dlp.exe')

        if os.path.exists(expected_bundled_path):
            YT_DLP_COMMAND = expected_bundled_path
            app_logger.info("Using bundled yt-dlp executable: %s" % YT_DLP_COMMAND) # Keep unwrapped
        else:
            # Log a warning if the bundled executable isn't found.
            # We keep the default 'yt-dlp' command, which might still work if
            # the user has yt-dlp in their system PATH for some reason, but it's
            # not the intended behavior for a self-contained bundle.
            # Consider raising an error here instead if bundling yt-dlp is mandatory.
            app_logger.warning("Bundled yt-dlp executable not found at %s. Falling back to system PATH 'yt-dlp'." % expected_bundled_path) # Keep unwrapped
    else:
         # Should not happen in typical frozen environments, but as a safeguard
         app_logger.warning("Could not determine base path in frozen environment. Falling back to system PATH 'yt-dlp'.") # Keep unwrapped

# --- End Logic to find the yt-dlp executable ---


# --- MODIFICATION: yt-dlp auto-update logic ---
_YT_DLP_UPDATE_CHECK_PERFORMED = False

def _check_and_update_yt_dlp():
    """
    Checks for yt-dlp updates once per session using the '-U' command.
    This is a blocking call and will be skipped if already run.
    """
    global _YT_DLP_UPDATE_CHECK_PERFORMED
    if _YT_DLP_UPDATE_CHECK_PERFORMED:
        return

    app_logger.info("Performing one-time check for yt-dlp updates with command: '%s -U'" % YT_DLP_COMMAND)
    command = [YT_DLP_COMMAND, "-U"]
    process = None
    try:
        # Use a reasonable timeout for the update check to avoid long hangs.
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=creation_flags)
        stdout, stderr = process.communicate(timeout=45)

        if process.returncode == 0:
            output = stdout.strip()
            if "is up to date" in output:
                app_logger.info("yt-dlp is up to date.")
            elif "Updated" in output:
                app_logger.info(f"yt-dlp was successfully updated. Output: {output}")
            else:
                app_logger.info(f"yt-dlp update check result: {output}")
        else:
            app_logger.warning(f"yt-dlp update check may have failed. RC: {process.returncode}. Stderr: {stderr.strip()}")

    except subprocess.TimeoutExpired:
        app_logger.warning("yt-dlp update check timed out. Continuing with existing version.")
        if process:
            process.kill()
    except FileNotFoundError:
         app_logger.error(f"yt-dlp command not found during update check. Command: {YT_DLP_COMMAND}")
    except Exception as e:
        app_logger.error(f"An unexpected error occurred during yt-dlp update check: {e}", exc_info=True)
    finally:
        # Mark as checked, regardless of outcome, to prevent re-running in this session.
        _YT_DLP_UPDATE_CHECK_PERFORMED = True
# --- END MODIFICATION ---


def get_video_info(video_url):
    """
    Fetches video information using yt-dlp without downloading the video.
    Returns a dictionary of video information or None if an error occurs.
    """
    _check_and_update_yt_dlp() # Check for updates first.

    # Before running the subprocess, explicitly check if the command exists
    # if we are using a specific path (i.e., not relying on the system PATH).
    if YT_DLP_COMMAND != 'yt-dlp' and not os.path.exists(YT_DLP_COMMAND):
         # Mark error for translation
         err_msg = _("yt-dlp executable not found. Expected at: %s") % YT_DLP_COMMAND
         app_logger.error(err_msg) # Keep unwrapped for log file
         raise DownloaderError(err_msg) # Raise our translatable error


    command = [
        YT_DLP_COMMAND, # <-- Use the determined command path
        '--quiet',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=default,mweb',
        '-j', # Output JSON
        video_url
    ]
    # Keep this info unwrapped
    app_logger.info(f"Fetching video info for URL: {video_url} with command: {' '.join(command)}")
    process = None # Define process for potential access in except block
    stdout = None  # Define stdout for potential access in except block
    try:
        # Note: creationflags=subprocess.CREATE_NO_WINDOW is Windows-specific
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=creation_flags)
        stdout, stderr = process.communicate(timeout=60)

        if process.returncode != 0:
            # Keep this error unwrapped. Mark N/A for translation as it's potentially user-visible in the log output via the UI.
            err_msg = "yt-dlp error fetching info for %s. RC: %s, Stderr: %s" % (video_url, process.returncode, stderr.strip() if stderr else _('N/A'))
            app_logger.error(err_msg)
            # Mark exception message for translation
            raise DownloaderError(err_msg) # Raise our translatable error

        if stdout:
            video_info = json.loads(stdout)
            # Keep this info unwrapped
            app_logger.info("Successfully fetched video info for %s. Title: %s" % (video_url, video_info.get('title')))
            return video_info
        else:
            # Keep this error unwrapped
            app_logger.error("yt-dlp returned no info for %s." % video_url)
            # Mark exception message for translation
            raise DownloaderError(_("yt-dlp returned no information for the video.")) # Raise our translatable error

    except FileNotFoundError:
        # Catch FileNotFoundError specifically if Popen itself fails to find the command
        # This is less likely with the explicit check above for non-dev mode,
        # but could still happen in dev mode if yt-dlp isn't in PATH.
        err_msg = _("yt-dlp command not found. Please ensure yt-dlp is installed and in your system PATH, or bundled correctly.")
        app_logger.error(err_msg) # Keep unwrapped
        raise DownloaderError(err_msg) # Raise our translatable error
    except subprocess.TimeoutExpired:
        # Keep this error unwrapped
        app_logger.error("Timeout fetching video info for %s." % video_url)
        if process: process.kill()
        # Mark exception message for translation
        raise DownloaderError(_("Timeout fetching video information.")) # Raise our translatable error
    except json.JSONDecodeError as e:
        log_stdout = stdout[:500] if stdout else _("N/A") # Mark "N/A" for translation if it shows up in UI
        # Keep this error unwrapped
        app_logger.error("Failed to parse yt-dlp JSON info output: %s. Output was: %s" % (e, log_stdout))
        # Mark exception message for translation
        raise DownloaderError(_("Failed to parse video information from yt-dlp. Output: %s") % log_stdout) # Raise our translatable error
    except Exception as e:
        # Catch any other unexpected errors
        # Keep this error unwrapped
        app_logger.error("An unexpected error occurred while fetching video info for %s: %s" % (video_url, e), exc_info=True)
        # Mark exception message for translation
        raise DownloaderError(_("Unexpected error fetching video info: %s") % e) # Raise our translatable error


def download_video(video_url, output_dir=TEMP_DOWNLOAD_DIR, desired_resolution=None, is_youtube_video=True):
    """
    Downloads a video from the given URL using yt-dlp.
    """
    _check_and_update_yt_dlp() # Check for updates first.

    if YT_DLP_COMMAND != 'yt-dlp' and not os.path.exists(YT_DLP_COMMAND):
         err_msg = _("yt-dlp executable not found. Expected at: %s") % YT_DLP_COMMAND
         app_logger.error(err_msg)
         raise DownloaderError(err_msg)


    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            app_logger.error("Failed to create output directory %s: %s" % (output_dir, e))
            raise DownloaderError(_("Cannot create download directory: %s") % e)

    output_template = os.path.join(output_dir, '%(id)s.%(ext)s')

    command = [
        YT_DLP_COMMAND,
        '--quiet',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=default,mweb',
        '-o', output_template,
    ]

    if is_youtube_video:
        if desired_resolution is None:
            loaded_resolution_setting = config_model.get_setting("youtube_download_quality")
            possible_qualities = ["Best Available", "1080p", "720p", "480p", "360p", "240p", "144p"]
            possible_qualities.append(_("Best Available"))
            
            desired_resolution = loaded_resolution_setting if loaded_resolution_setting in possible_qualities else "480p"

        app_logger.info("YouTube download: Using quality setting '%s'." % desired_resolution)

        # --- FIX: Check for both the original English string and its translated version ---
        # The untranslated string "Best Available" is the key for gettext.
        is_best_available = desired_resolution in ("Best Available", _("Best Available"))
        
        if is_best_available:
            app_logger.info("Interpreting quality setting as 'Best Available' for yt-dlp command.")
            command.extend(['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best'])
            command.extend(['--merge-output-format', 'mp4'])
        else:
            height = desired_resolution.replace('p', '')
            command.extend(['-f', f"bv[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]/bv*[height<={height}][ext=webm]+ba[ext=webm]/b[height<={height}][ext=webm]/best"])
            command.extend(['--merge-output-format', 'mp4'])
    else:
        app_logger.info("Direct URL download: %s. yt-dlp will attempt to download as is." % video_url)

    command.append(video_url)

    app_logger.info(f"Download command: {' '.join(command)}")

    process = None
    try:
        video_info = get_video_info(video_url)
        if not video_info:
            raise DownloaderError(_("Could not retrieve video info; cannot reliably predict output filename."))

        video_id = video_info.get('id', 'unknown_video_id_' + str(hash(video_url))[:8])
        predicted_ext = 'mp4'
        if '--merge-output-format' in command:
             try:
                 merge_format_index = command.index('--merge-output-format')
                 if merge_format_index + 1 < len(command):
                     predicted_ext = command[merge_format_index + 1].lower()
             except ValueError:
                 pass
        elif not is_youtube_video:
            predicted_ext = video_info.get('ext', 'mp4')

        final_filename = f"{video_id}.{predicted_ext}"
        expected_download_path = os.path.join(output_dir, final_filename)

        if os.path.exists(expected_download_path):
            app_logger.warning("File %s already exists. yt-dlp behavior on existing files may vary (overwrite/numbering)." % expected_download_path)

        app_logger.info("Download process started for %s. Expecting output at: %s" % (video_url, expected_download_path))
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=creation_flags)
        stdout, stderr = process.communicate(timeout=1800)

        if process.returncode != 0:
            err_msg = "yt-dlp download failed for %s. RC: %s\nStdout: %s\nStderr: %s" % (video_url, process.returncode, stdout.strip() if stdout else _('N/A'), stderr.strip() if stderr else _('N/A'))
            app_logger.error(err_msg)
            raise DownloaderError(err_msg)

        if os.path.exists(expected_download_path):
            app_logger.info("Download successful: %s" % expected_download_path)
            return expected_download_path
        else:
            app_logger.warning("Expected file '%s' not found after download." % expected_download_path)
            found_path = None
            files_in_dir = os.listdir(output_dir)
            for f_name in files_in_dir:
                if f_name == final_filename:
                     found_path = os.path.join(output_dir, f_name)
                     break
                if f_name.startswith(video_id + '.'):
                    found_path = os.path.join(output_dir, f_name)
                    break
            if found_path and os.path.exists(found_path):
                app_logger.info("Download successful (found by ID match): %s" % found_path)
                return found_path

            app_logger.error("Download seemed to complete (yt-dlp RC=0) but no output file matching ID '%s' found in '%s'." % (video_id, output_dir))
            app_logger.debug(f"Files in output directory '{output_dir}': {files_in_dir}")
            raise DownloaderError(_("Download completed but output file not found. Check logs for details in %s.") % output_dir)

    except FileNotFoundError:
         err_msg = _("yt-dlp command not found. Please ensure yt-dlp is installed and in your system PATH, or bundled correctly at %s.") % YT_DLP_COMMAND
         app_logger.error(err_msg)
         raise DownloaderError(err_msg)
    except subprocess.TimeoutExpired:
        app_logger.error("Timeout downloading video %s." % video_url)
        if process: process.kill()
        raise DownloaderError(_("Timeout downloading video: %s") % video_url)
    except DownloaderError:
        raise
    except Exception as e:
        app_logger.error("An unexpected error occurred during download for %s: %s" % (video_url, e), exc_info=True)
        raise DownloaderError(_("Unexpected error during download for %s: %s") % (video_url, e))

# --- Standalone Test Block ---
if __name__ == '__main__':
    try:
        from audio_describer.models import config_model as test_config_model
        if not test_config_model.app_settings.get("youtube_download_quality"):
            print(_("Standalone test: config_model.app_settings seems empty, loading defaults for test."))
            test_config_model.app_settings.update(test_config_model.DEFAULT_SETTINGS)
    except ImportError:
        print("Warning: Could not import config_model for standalone test. Skipping settings load.")
        class DummyConfigModel:
             DEFAULT_SETTINGS = {"youtube_download_quality": "480p"}
             app_settings = {}
             def get_setting(self, key):
                  return self.app_settings.get(key, self.DEFAULT_SETTINGS.get(key))
        test_config_model = DummyConfigModel()
        config_model = test_config_model

    try:
        from audio_describer.utils.logger import app_logger as test_app_logger
    except ImportError:
         import logging
         test_app_logger = logging.getLogger(__name__)
         test_app_logger.setLevel(logging.DEBUG)
         if not test_app_logger.handlers:
              handler = logging.StreamHandler(sys.stdout)
              formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
              handler.setFormatter(formatter)
              test_app_logger.addHandler(handler)
         print("Warning: Could not import app_logger for standalone test. Using standard logging.")
         app_logger = test_app_logger


    app_logger.info("Running self-test for youtube_downloader.py...")
    app_logger.info("Using yt-dlp command: %s" % YT_DLP_COMMAND)
    app_logger.info("Is Frozen: %s" % IS_FROZEN)


    test_youtube_url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

    downloaded_path_yt = None

    try:
        print(_("\nFetching info for: %s") % test_youtube_url)
        current_config_model = locals().get('test_config_model', config_model)

        info = get_video_info(test_youtube_url)
        if info:
            print(_("Successfully fetched info for YouTube video: %s") % info.get('title', _('N/A')))
        else:
            print(_("Failed to fetch info for %s") % test_youtube_url)

        download_quality_setting = current_config_model.get_setting('youtube_download_quality')
        print(_("\nAttempting to download YouTube video: %s (Quality from settings: %s)") % (test_youtube_url, download_quality_setting))
        downloaded_path_yt = download_video(test_youtube_url, is_youtube_video=True, desired_resolution=None)
        if downloaded_path_yt and os.path.exists(downloaded_path_yt):
            print(_("YouTube video downloaded successfully to: %s") % downloaded_path_yt)
            print(_("File size: %s MB") % f"{os.path.getsize(downloaded_path_yt) / (1024*1024):.2f}")
        else:
            print(_("YouTube download failed or file not found at: %s") % downloaded_path_yt)


    except DownloaderError as e:
        print(_("A DownloaderError occurred during self-test: %s") % e)
    except Exception as e:
        print(_("An unexpected error occurred during self-test: %s") % e)
    finally:
        if downloaded_path_yt and os.path.exists(downloaded_path_yt):
            print(_("Cleaning up test file: %s") % downloaded_path_yt)
            try: os.remove(downloaded_path_yt)
            except Exception as e_rem:
                print(_("Could not remove %s: %s") % (downloaded_path_yt, e_rem))

        print(_("\nDownloader self-test finished. Check console and app_log.txt for details."))
        print(_("Temporary download directory used: %s") % TEMP_DOWNLOAD_DIR)
        print(_("yt-dlp command used: %s") % YT_DLP_COMMAND)