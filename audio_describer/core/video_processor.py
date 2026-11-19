# audio_describer/core/video_processor.py
from audio_describer.i18n_setup import _
import os
import subprocess
import tempfile
import sys
import json
import math

from audio_describer import config
from audio_describer.utils.logger import app_logger
from audio_describer.core import youtube_downloader, timing
from audio_describer.models import config_model
from audio_describer.utils.system_utils import run_command, get_ffmpeg_path

FFMPEG_COMMAND = get_ffmpeg_path('ffmpeg')
FFPROBE_COMMAND = get_ffmpeg_path('ffprobe')

# Under %TEMP%, not the working folder: launched from Program Files or System32 the old
# os.getcwd() location raised PermissionError at import and the app failed to start.
TEMP_PROCESSING_BASE_DIR = os.path.join(tempfile.gettempdir(), "OmniDescriber", config.TEMP_DIR_NAME)
TEMP_VIDEO_DIR = os.path.join(TEMP_PROCESSING_BASE_DIR, "processed_videos")
try:
    os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)
except OSError as e:
    app_logger.warning(f"Could not create {TEMP_VIDEO_DIR}: {e}")

try:
    ffmpeg_process = run_command([FFMPEG_COMMAND, "-version"])
    FFMPEG_IS_AVAILABLE = "ffmpeg version" in ffmpeg_process.stdout.lower() and ffmpeg_process.returncode == 0
    if not FFMPEG_IS_AVAILABLE:
        app_logger.warning(f"FFmpeg check failed. Command: '{FFMPEG_COMMAND}'. RC: {ffmpeg_process.returncode}. Stderr: {ffmpeg_process.stderr.strip()}")

    ffprobe_process = run_command([FFPROBE_COMMAND, "-version"])
    FFPROBE_IS_AVAILABLE = "ffprobe version" in ffprobe_process.stdout.lower() and ffprobe_process.returncode == 0
    if not FFPROBE_IS_AVAILABLE:
        app_logger.warning(f"FFprobe check failed. Command: '{FFPROBE_COMMAND}'. RC: {ffprobe_process.returncode}. Stderr: {ffprobe_process.stderr.strip()}")
except Exception as e:
    app_logger.error(f"A critical error occurred during FFmpeg/FFprobe availability check: {e}", exc_info=True)
    FFMPEG_IS_AVAILABLE = False
    FFPROBE_IS_AVAILABLE = False


def image_as_jpeg_bytes(path, max_side=1568):
    """Any picture ffmpeg reads, as JPEG bytes with its longest side at most max_side px
    (vision APIs accept JPEG everywhere and bill by pixels). None when ffmpeg cannot convert it."""
    if FFMPEG_IS_AVAILABLE:
        with tempfile.TemporaryDirectory(prefix="omni_img_") as tmp:
            out = os.path.join(tmp, "image.jpg")
            scale = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
            result = run_command([FFMPEG_COMMAND, "-hide_banner", "-loglevel", "error", "-i", path,
                                  "-frames:v", "1", "-vf", scale, "-q:v", "4", out])
            if result.returncode == 0 and os.path.exists(out):
                with open(out, "rb") as f:
                    return f.read()
            app_logger.warning(f"Image conversion failed: {result.stderr[-300:]}")
    return None


def get_video_duration(video_path):
    if not FFPROBE_IS_AVAILABLE:
        app_logger.warning("ffprobe not available, cannot get video duration for chunking.")
        return 0.0
    
    command = [
        FFPROBE_COMMAND, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    process = run_command(command)
    if process.returncode == 0 and process.stdout.strip():
        try:
            return float(process.stdout.strip())
        except ValueError:
            return 0.0
    return 0.0


def has_audio_stream(video_path):
    if not FFPROBE_IS_AVAILABLE:
        return True
    process = run_command([FFPROBE_COMMAND, "-v", "error", "-select_streams", "a",
                           "-show_entries", "stream=index", "-of", "csv=p=0", video_path])
    return process.returncode != 0 or bool(process.stdout.strip())


def detect_quiet_windows(video_path, duration=None):
    """Windows [(start, end), ...] where a description can go, found locally (no tokens).

    Silero VAD when available: windows without speech, music included (audit §10.2).
    Otherwise ffmpeg silencedetect: true silences only. Returns None when it cannot be
    determined (no ffmpeg, no audio track, error) so the model judges pauses itself.
    """
    if not FFMPEG_IS_AVAILABLE or not has_audio_stream(video_path):
        return None
    threshold_db = config_model.get_setting("silence_threshold_db") or -35
    min_duration = config_model.get_setting("silence_min_duration_sec") or 0.8
    from . import vad
    windows = vad.detect_speech_free_windows(video_path, duration, min_duration)
    if windows is not None:
        return windows
    cmd = [FFMPEG_COMMAND, "-hide_banner", "-nostats", "-i", video_path, "-vn", "-sn", "-dn",
           "-af", f"silencedetect=n={threshold_db}dB:d={min_duration}", "-f", "null", "-"]
    process = run_command(cmd)
    if process.returncode != 0:
        app_logger.warning(f"silencedetect failed: {process.stderr[-500:]}")
        return None
    windows = timing.parse_silencedetect(process.stderr, duration)
    app_logger.info(f"silencedetect: {len(windows)} quiet windows (n={threshold_db}dB, d={min_duration}s)")
    return windows

SCENE_THRESHOLD = 0.3  # ffmpeg scene score; 0.3 is the usual cut threshold


def detect_shot_changes(video_path):
    """Shot changes in seconds (audit §10.2): natural anchors for a description. None on failure.
    Decodes the whole video at a small size: about a minute per hour of film."""
    if not FFMPEG_IS_AVAILABLE:
        return None
    cmd = [FFMPEG_COMMAND, "-hide_banner", "-nostats", "-i", video_path, "-an", "-sn", "-dn",
           "-vf", f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"]
    process = run_command(cmd)
    if process.returncode != 0:
        app_logger.warning(f"Shot detection failed: {process.stderr[-500:]}")
        return None
    cuts = timing.parse_scene_changes(process.stderr)
    app_logger.info(f"Shot detection: {len(cuts)} changes")
    return cuts


def _standardize_video_format(input_path, is_local_file=False):
    if not FFMPEG_IS_AVAILABLE:
        app_logger.warning("FFmpeg not available. Using original file for playback and analysis.")
        return input_path, not is_local_file

    _, extension = os.path.splitext(input_path)
    
    if extension.lower() == '.mp4':
        app_logger.info(f"Video '{os.path.basename(input_path)}' is already in MP4 container. Skipping standardization.")
        return input_path, not is_local_file

    app_logger.info(f"Repackaging '{os.path.basename(input_path)}' into MP4 container for maximum compatibility (fast process).")
    
    output_path = os.path.join(tempfile.gettempdir(), f"repackaged_{os.urandom(4).hex()}.mp4")

    command = [
        FFMPEG_COMMAND, "-y", "-i", input_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]

    process = run_command(command)
    if process.returncode != 0:
        app_logger.error(f"Failed to repackage video. Using original file. Error: {process.stderr}")
        return input_path, not is_local_file

    app_logger.info(f"Successfully repackaged video to temporary file: '{output_path}'")
    
    if not is_local_file:
        cleanup_temp_file(input_path)
        
    return output_path, True

def preprocess_video_for_ai(original_video_path, status_update_callback=None):
    """Pre-processes video for AI analysis.

    FPS reduction is now handled natively by Gemini's videoMetadata parameter,
    so this function only handles audio stripping when requested.
    """
    if not FFMPEG_IS_AVAILABLE:
        if status_update_callback: status_update_callback(_("FFmpeg not found; using original video for AI."))
        return original_video_path, False

    should_silence = config_model.get_setting("send_silenced_video_to_ai")

    if not should_silence:
        app_logger.info("No pre-processing needed for AI video. FPS is handled by Gemini videoMetadata.")
        return original_video_path, False

    try:
        if status_update_callback: status_update_callback(_("Pre-processing video for AI analysis (stripping audio)..."))

        temp_dir = tempfile.gettempdir()
        video_basename = os.path.basename(original_video_path)
        name, ext = os.path.splitext(video_basename)
        processed_video_path = os.path.join(temp_dir, f"{name}_processed_{os.urandom(4).hex()}{ext}")

        ffmpeg_cmd = [
            FFMPEG_COMMAND, "-y", "-i", original_video_path,
            "-c:v", "copy", "-an",
            processed_video_path
        ]

        app_logger.info(f"Running FFmpeg pre-process command: {' '.join(ffmpeg_cmd)}")
        process = run_command(ffmpeg_cmd)

        if process.returncode != 0:
            err_msg = _("Failed to pre-process video; using original. Error: %s") % process.stderr
            if status_update_callback: status_update_callback(err_msg)
            app_logger.error(err_msg)
            return original_video_path, False

        if not os.path.exists(processed_video_path) or os.path.getsize(processed_video_path) < 1024:
            if status_update_callback: status_update_callback(_("Video pre-processing error; using original."))
            return original_video_path, False

        if status_update_callback: status_update_callback(_("Video pre-processing complete."))
        return processed_video_path, True

    except Exception as e:
        err_msg = _("Error during video pre-processing; using original. Error: %s") % e
        if status_update_callback: status_update_callback(err_msg)
        app_logger.error(err_msg, exc_info=True)
        return original_video_path, False


def process_local_file(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(_("Video file not found: %s") % file_path)
    return _standardize_video_format(file_path, is_local_file=True)

def process_direct_url(video_url):
    try:
        downloaded_file_path = youtube_downloader.download_video(video_url, output_dir=youtube_downloader.TEMP_DOWNLOAD_DIR, is_youtube_video=False)
        return _standardize_video_format(downloaded_file_path, is_local_file=False)
    except youtube_downloader.DownloaderError as e:
        raise youtube_downloader.DownloaderError(_("Failed to download from direct URL %s: %s") % (video_url, e))

def process_youtube_url(youtube_url, desired_resolution=None):
    try:
        app_logger.info(f"Attempting to download '{youtube_url}' as a YouTube video...")
        downloaded_file_path = youtube_downloader.download_video(
            video_url=youtube_url, 
            output_dir=youtube_downloader.TEMP_DOWNLOAD_DIR,
            desired_resolution=desired_resolution, 
            is_youtube_video=True
        )
        return _standardize_video_format(downloaded_file_path, is_local_file=False)
    except youtube_downloader.DownloaderError as e:
        app_logger.warning(f"Initial YouTube download failed for '{youtube_url}': {e}. Retrying as a direct URL...")
        
        try:
            downloaded_file_path = youtube_downloader.download_video(
                video_url=youtube_url, 
                output_dir=youtube_downloader.TEMP_DOWNLOAD_DIR, 
                is_youtube_video=False
            )
            return _standardize_video_format(downloaded_file_path, is_local_file=False)
        except youtube_downloader.DownloaderError as e2:
            final_error_message = _("Failed to download from YouTube URL %(url)s. "
                                    "Initial error: %(initial_error)s. "
                                    "Fallback attempt also failed: %(fallback_error)s") % {
                                        'url': youtube_url, 
                                        'initial_error': e, 
                                        'fallback_error': e2
                                    }
            raise youtube_downloader.DownloaderError(final_error_message)

def is_inside(path, folder):
    """True when path is inside folder (real path containment, case-insensitive on Windows),
    not merely a string containing the folder name."""
    try:
        path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
        return path != folder and os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives
        return False


def cleanup_temp_file(file_path):
    """Delete a file this app created in one of its temporary folders; anything else is left alone."""
    if not isinstance(file_path, str): return
    abs_file_path = os.path.abspath(file_path)
    temp_dirs = (youtube_downloader.TEMP_DOWNLOAD_DIR, TEMP_VIDEO_DIR, tempfile.gettempdir())

    if os.path.isfile(abs_file_path) and any(is_inside(abs_file_path, d) for d in temp_dirs if d):
        try:
            os.remove(abs_file_path)
            app_logger.info(f"Cleaned up temporary file: {abs_file_path}")
        except Exception as e:
            app_logger.error("Error cleaning up temporary file %s: %s" % (abs_file_path, e))