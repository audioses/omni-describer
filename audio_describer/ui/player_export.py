# audio_describer/ui/player_export.py
from ..i18n_setup import _ as gettext_
import wx
import os
import sys
import datetime
import tempfile
import threading
import subprocess

from ..utils.logger import app_logger
from ..core import tts_engine, video_processor, timing
from ..core.tts_engine import TTSError
from ..models import config_model
from ..utils.system_utils import run_command
from .export_options_dialog import ExportOptionsDialog

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

EVT_EXPORT_DONE_ID = wx.NewIdRef()
EVT_EXPORT_DONE = wx.PyEventBinder(EVT_EXPORT_DONE_ID, 0)

class ExportDoneEvent(wx.PyCommandEvent):
    def __init__(self, output_path="", error=None):
        super().__init__(EVT_EXPORT_DONE_ID, 0)
        self.output_path = output_path
        self.error = error

FFMPEG_COMMAND = video_processor.FFMPEG_COMMAND

# --- START: SECURITY FIX ---
# Explicitly tell pydub where to find the bundled ffmpeg.exe.
# This is crucial because pydub does not know about the application's
# internal 'bin' directory and will fail to find ffmpeg when exporting
# MP3s, even if our own code can find it for other tasks.
if PYDUB_AVAILABLE:
    AudioSegment.converter = FFMPEG_COMMAND
# --- END: SECURITY FIX ---

def _normalize_path_for_ffmpeg(path):
    """
    Ensures the path is absolute and uses forward slashes, which is more
    robust for FFmpeg's concat demuxer.
    """
    return os.path.abspath(path).replace('\\', '/')

def format_time_for_srt(total_seconds):
    if total_seconds < 0: total_seconds = 0
    delta = datetime.timedelta(seconds=float(total_seconds))
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = delta.microseconds // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def start_export_process(player_instance):
    if not player_instance.descriptions:
        wx.MessageBox(gettext_("No descriptions to export."), gettext_("Export Note"), wx.OK | wx.ICON_INFORMATION, player_instance); return
        
    with ExportOptionsDialog(player_instance) as dlg:
        if dlg.ShowModal() != wx.ID_OK:
            return
        options = dlg.GetValues()

    # Determine which handler to call based on the simplified options
    if options['category'] == 'text':
        _handle_text_export(player_instance, options)
    elif options['category'] == 'av':
        _handle_av_export(player_instance, options)

def _handle_text_export(player_instance, options):
    video_basename = os.path.splitext(os.path.basename(player_instance.video_path))[0]
    file_format = options['format']
    descriptions = list(player_instance.descriptions)

    wildcard_map = {
        'srt': (gettext_("SRT Subtitles (*.srt)|*.srt"), f"{video_basename}_descriptions.srt"),
        'txt_line': (gettext_("Text files (*.txt)|*.txt"), f"{video_basename}_descriptions_lines.txt"),
    }
    wildcard, suggested_file = wildcard_map.get(file_format, (gettext_("All files|*.*"), "export.txt"))


    with wx.FileDialog(player_instance, gettext_("Save Exported Text File"), wildcard=wildcard, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=suggested_file) as dlg:
        if dlg.ShowModal() == wx.ID_CANCEL: return
        pathname = dlg.GetPath()
        try:
            with open(pathname, 'w', encoding='utf-8') as f:
                if file_format == 'srt':
                    for i, (start, end, text) in enumerate(descriptions):
                        f.write(f"{i + 1}\n{format_time_for_srt(start)} --> {format_time_for_srt(end)}\n{text}\n\n")
                elif file_format == 'txt_line':
                    for _, _, text in descriptions:
                        f.write(f"{text}\n")
            wx.MessageBox(gettext_("File successfully exported to:\n%s") % pathname, gettext_("Export Successful"), wx.OK | wx.ICON_INFORMATION, player_instance)
        except IOError as e:
            wx.MessageBox(gettext_("Error saving file: %s") % e, gettext_("Export Error"), wx.OK | wx.ICON_ERROR, player_instance)

def _handle_av_export(player_instance, options):
    video_basename = os.path.splitext(os.path.basename(player_instance.video_path))[0]
    
    # Only one AV export option now: MP3 (ducked)
    file_format_map = {
        "mp3_ducked": (gettext_("MP3 Audio (*.mp3)|*.mp3"), f"{video_basename}_described_audio.mp3"),
    }
    
    wildcard, suggested_file = file_format_map.get(options['format'])

    with wx.FileDialog(player_instance, gettext_("Save Exported File"), wildcard=wildcard, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=suggested_file) as dlg:
        if dlg.ShowModal() == wx.ID_CANCEL: return
        output_path = dlg.GetPath()

    player_instance.progress_dialog = wx.ProgressDialog(
        gettext_("Exporting File"), gettext_("Starting..."),
        maximum=len(player_instance.descriptions) * 3 + 5,
        parent=player_instance,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_ELAPSED_TIME | wx.PD_ESTIMATED_TIME | wx.PD_CAN_ABORT
    )
    
    descriptions_copy = list(player_instance.descriptions)
    
    # Directly call the only AV export thread target.
    thread = threading.Thread(target=_perform_mp3_export_thread, args=(player_instance, output_path, descriptions_copy, options))
    thread.daemon = True
    thread.start()

def _synthesize_all_descriptions(descriptions, progress_dialog):
    temp_wav_files = []
    description_segments = []
    
    for i, desc in enumerate(descriptions):
        start, end, text = desc
        if progress_dialog.WasCancelled(): raise InterruptedError("User cancelled.")
        wx.CallAfter(progress_dialog.Update, i + 1, gettext_("Synthesizing description %d of %d...") % (i + 1, len(descriptions)))
        
        temp_wav = os.path.join(tempfile.gettempdir(), f"desc_{i}_{os.urandom(4).hex()}.wav")
        temp_wav_files.append(temp_wav)
        
        if not tts_engine.synthesize_description_to_wav(text, temp_wav, gender=timing.voice_gender(desc)):
            raise TTSError(gettext_("Failed to synthesize description #%d.") % (i + 1))
            
        desc_segment = AudioSegment.from_file(temp_wav)
        description_segments.append({"start_sec": start, "segment": desc_segment})
        
    return description_segments, temp_wav_files

def _apply_ducking_with_fades(original_audio, description_details, ducking_db):
    FADE_DURATION_MS = 150
    
    for i in range(len(description_details) - 1):
        current_desc = description_details[i]
        next_desc = description_details[i+1]
        current_end_sec = current_desc["start_sec"] + current_desc["segment"].duration_seconds
        next_start_sec = next_desc["start_sec"]

        if current_end_sec > next_start_sec:
            available_time_sec = next_start_sec - current_desc["start_sec"]
            if available_time_sec <= 0.1:
                current_desc["segment"] = AudioSegment.silent(duration=1)
                continue
            
            new_duration_ms = max(50, available_time_sec * 1000)
            current_desc["segment"] = current_desc["segment"][:new_duration_ms].fade_out(50)

    duck_intervals = []
    for desc_info in description_details:
        start_ms = int(desc_info["start_sec"] * 1000)
        end_ms = start_ms + len(desc_info["segment"])
        duck_intervals.append([start_ms, end_ms])

    merged_intervals = []
    if duck_intervals:
        duck_intervals.sort()
        current_start, current_end = duck_intervals[0]
        for next_start, next_end in duck_intervals[1:]:
            if next_start < (current_end + FADE_DURATION_MS):
                current_end = max(current_end, next_end)
            else:
                merged_intervals.append((current_start, current_end))
                current_start, current_end = next_start, next_end
        merged_intervals.append((current_start, current_end))

    processed_audio = AudioSegment.empty()
    last_processed_ms = 0

    for start_ms, end_ms in merged_intervals:
        pre_fade_end = start_ms - FADE_DURATION_MS
        if pre_fade_end > last_processed_ms:
            processed_audio += original_audio[last_processed_ms:pre_fade_end]

        fade_out_start_actual = max(last_processed_ms, pre_fade_end)
        if fade_out_start_actual < start_ms:
            fade_out_segment = original_audio[fade_out_start_actual:start_ms]
            if len(fade_out_segment) > 0:
                processed_audio += fade_out_segment.fade(to_gain=ducking_db, start=0, end=len(fade_out_segment))

        if start_ms < end_ms:
            processed_audio += original_audio[start_ms:end_ms].apply_gain(ducking_db)

        fade_in_end = end_ms + FADE_DURATION_MS
        if end_ms < fade_in_end:
            fade_in_segment = original_audio[end_ms:fade_in_end]
            if len(fade_in_segment) > 0:
                processed_audio += fade_in_segment.fade(from_gain=ducking_db, start=0, end=len(fade_in_segment))
        
        last_processed_ms = fade_in_end
    
    if last_processed_ms < len(original_audio):
        processed_audio += original_audio[last_processed_ms:]

    if len(processed_audio) < len(original_audio):
        processed_audio += AudioSegment.silent(duration=len(original_audio) - len(processed_audio))

    for desc_info in description_details:
        desc_start_ms = int(desc_info["start_sec"] * 1000)
        desc_end_ms = desc_start_ms + len(desc_info["segment"])
        
        if desc_end_ms > len(processed_audio):
            desc_info["segment"] = desc_info["segment"][:len(processed_audio) - desc_start_ms]
            if len(desc_info["segment"]) <= 0:
                continue
                
        processed_audio = processed_audio.overlay(desc_info["segment"], position=desc_start_ms)
        
    return processed_audio, []

def _perform_mp3_export_thread(player_instance, output_path, descriptions, options):
    temp_files = []
    progress_dialog = player_instance.progress_dialog
    try:
        if not PYDUB_AVAILABLE:
            raise TTSError(gettext_("pydub library not available. Audio export functionality is disabled."))

        wx.CallAfter(progress_dialog.Update, 0, gettext_("Extracting original audio..."))
        original_audio_path = os.path.join(tempfile.gettempdir(), f"original_audio_{os.urandom(4).hex()}.wav")
        temp_files.append(original_audio_path)
        cmd = [FFMPEG_COMMAND, "-y", "-i", player_instance.video_path, "-vn", "-c:a", "pcm_s16le", original_audio_path]
        process_result = run_command(cmd)
        if process_result.returncode != 0: 
            app_logger.error(f"FFmpeg failed to extract audio: {process_result.stderr}")
            raise TTSError(gettext_("FFmpeg failed to extract audio. Check FFmpeg availability and video file."))
        
        description_details, synth_files = _synthesize_all_descriptions(descriptions, progress_dialog)
        temp_files.extend(synth_files)

        wx.CallAfter(progress_dialog.Update, len(descriptions) + 2, gettext_("Mixing audio tracks..."))
        original_audio = AudioSegment.from_file(original_audio_path)
        ducking_db = float(config_model.get_setting("mp3_audio_ducking_level_db"))
        
        mixed_audio, _ = _apply_ducking_with_fades(original_audio, description_details, ducking_db)
        
        wx.CallAfter(progress_dialog.Update, len(descriptions) + 3, gettext_("Saving to MP3..."))
        mixed_audio.export(output_path, format="mp3", bitrate="192k")
        
        wx.CallAfter(progress_dialog.Update, len(descriptions) * 3 + 5, gettext_("Export Complete."))

        wx.PostEvent(player_instance, ExportDoneEvent(output_path=output_path))
    except Exception as e:
        app_logger.error(f"Error during MP3 export thread: {e}", exc_info=True)
        wx.PostEvent(player_instance, ExportDoneEvent(error=e))
    finally:
        for path in temp_files:
            if os.path.exists(path):
                try: os.remove(path)
                except Exception as e: 
                    app_logger.warning(f"Failed to remove temp file {path}: {e}")