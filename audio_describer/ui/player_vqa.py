# audio_describer/ui/player_vqa.py
from ..i18n_setup import _
import wx
import os
import sys
import tempfile
import threading
import subprocess
import json

from ..utils.logger import app_logger
from ..core import audio_describer
from ..models import config_model
from ..utils import sound_player
from ..core.video_processor import FFMPEG_COMMAND
from ..utils.system_utils import run_command
from .ask_more_dialog import AskMoreDialog
from .scene_explorer_dialog import SceneExplorer
from .explore_scene_options_dialog import ExploreSceneOptionsDialog
from . import call_if_open

lang_map = {"en": "English", "es": "Spanish", "fr": "French", "ar": "Arabic", "pt": "Portuguese", "it": "Italian", "ru": "Russian", "uk": "Ukrainian", "vi": "vietnamese", "tr": "Turkish"}

try:
    import vlc
except ImportError:
    vlc = None

class DownloaderError(Exception): pass

def handle_ask_more(player_instance):
    if not player_instance.vlc_player: return
    
    was_playing = player_instance.vlc_player.is_playing()
    if was_playing:
        player_instance.vlc_player.pause()
    
    current_time_ms = player_instance.vlc_player.get_time()
    if current_time_ms < 0:
        wx.MessageBox(_("Cannot determine current video time."), _("Error"), wx.OK | wx.ICON_ERROR, player_instance); return
    
    player_instance._original_state_before_ask_more = was_playing
    
    if player_instance.ask_more_dialog_instance and player_instance.ask_more_dialog_instance.IsShown():
        player_instance.ask_more_dialog_instance.Raise()
    else:
        player_instance.ask_more_dialog_instance = AskMoreDialog(player_instance, current_video_time_sec=current_time_ms / 1000.0)
        player_instance.ask_more_dialog_instance.Bind(wx.EVT_CLOSE, player_instance.OnAskMoreDialogClosed)
        player_instance.ask_more_dialog_instance.Show()

def handle_ask_more_submission(player_instance, event):
    user_question = event.GetQuestion()
    context_duration_sec = event.GetContextDuration()
    current_time_ms = player_instance.vlc_player.get_time()
    media_total_duration_ms = player_instance.vlc_player.get_length()
    
    if player_instance.ask_more_dialog_instance and player_instance.ask_more_dialog_instance.IsShown():
        player_instance.ask_more_dialog_instance.OnProcessingStart()
    else: return
    threading.Thread(target=_handle_ask_more_thread,
                     args=(player_instance, user_question, current_time_ms, context_duration_sec, media_total_duration_ms, _),
                     daemon=True).start()

def _handle_ask_more_thread(player_instance, user_question, segment_start_ms, context_duration_sec, media_total_duration_ms, trans_func):
    temp_segment_path = None
    ai_answer = trans_func("AI processing could not be completed.")
    vqa_usage_data = {} 
    is_error_response = True

    try:
        sound_player.play("aistart.mp3")
        start_time_ffmpeg = segment_start_ms / 1000.0
        available_duration_sec = (media_total_duration_ms - segment_start_ms) / 1000.0
        actual_duration_ffmpeg_sec = min(context_duration_sec, available_duration_sec)
        if actual_duration_ffmpeg_sec <= 0: raise Exception(trans_func("No video context available."))
        temp_segment_path = os.path.join(tempfile.gettempdir(), f"ask_more_segment_{os.urandom(4).hex()}.mp4")
        
        ffmpeg_extract_cmd = [FFMPEG_COMMAND, "-y", "-ss", str(start_time_ffmpeg), "-i", player_instance.video_path, "-t", str(actual_duration_ffmpeg_sec), "-c:v", "copy", "-c:a", "copy", temp_segment_path]
        
        process_extract = run_command(ffmpeg_extract_cmd)
        if process_extract.returncode != 0: raise Exception(trans_func("FFmpeg failed to extract segment: %s") % (process_extract.stderr or process_extract.stdout))
        
        target_language_code = config_model.get_setting("application_language") or "en"
        
        target_language_name = lang_map.get(target_language_code.lower(), "English")
        vqa_prompt = f"Based on this short video clip, answer the user's question concisely in {target_language_name}: \"{user_question}\""
        ai_answer, vqa_usage_data = audio_describer.ask_gemini_about_video_segment(temp_segment_path, vqa_prompt)
        is_error_response = False
    except (DownloaderError, Exception) as e:
        ai_answer = str(e)
        is_error_response = True
    finally:
        if not is_error_response:
            sound_player.play("aistop.mp3")
        else:
            sound_player.play("error.mp3")

        if temp_segment_path:
            try: os.remove(temp_segment_path)
            except Exception: pass
            
        def update_ui_after_vqa(answer, usage, is_error):
            dialog = player_instance.ask_more_dialog_instance
            if dialog and dialog.IsShown(): dialog.SetAnswer(answer, is_error)
            player_instance.AppendVQAUsage(usage) 
            if hasattr(player_instance, '_original_state_before_ask_more') and player_instance._original_state_before_ask_more:
                player_instance.vlc_player.play()
        call_if_open(player_instance, update_ui_after_vqa, ai_answer, vqa_usage_data, is_error_response)

def handle_explore_scene(player_instance):
    if not player_instance.vlc_player: return
    
    with ExploreSceneOptionsDialog(player_instance) as options_dlg:
        if options_dlg.ShowModal() != wx.ID_OK:
            return
        options = options_dlg.GetValues()

    if player_instance.vlc_player.is_playing():
        player_instance.vlc_player.pause()
    current_time_ms = player_instance.vlc_player.get_time()
    media_total_duration_ms = player_instance.vlc_player.get_length()

    if current_time_ms < 0:
        wx.MessageBox(_("Cannot determine current video time."), _("Error"), wx.OK | wx.ICON_ERROR, player_instance); return
    
    progress_dialog = wx.ProgressDialog(_("Analyzing Scene"), _("Extracting video clip for analysis..."), parent=player_instance, style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE)
    
    threading.Thread(target=_handle_explore_scene_thread, args=(player_instance, current_time_ms, media_total_duration_ms, progress_dialog, options, _), daemon=True).start()

def _handle_explore_scene_thread(player_instance, segment_start_ms, media_total_duration_ms, progress_dialog, options, trans_func):
    temp_segment_path = None
    try:
        sound_player.play("aistart.mp3")

        duration_req = options["duration"]
        user_prompt = options["prompt"]

        call_if_open(player_instance, progress_dialog.Pulse, trans_func("Extracting {duration}s clip...").format(duration=duration_req))
        start_time_ffmpeg = max(0, (segment_start_ms / 1000.0) - (duration_req / 2.0))
        
        remaining_duration_sec = (media_total_duration_ms / 1000.0) - start_time_ffmpeg
        actual_duration_to_extract = min(duration_req, remaining_duration_sec)
        
        if actual_duration_to_extract <= 0:
            raise ValueError(trans_func("No time remaining in the video to analyze from the selected point."))
            
        temp_segment_path = os.path.join(tempfile.gettempdir(), f"explore_scene_{os.urandom(4).hex()}.mp4")
        ffmpeg_extract_cmd = [FFMPEG_COMMAND, "-y", "-ss", str(start_time_ffmpeg), "-i", player_instance.video_path, "-t", str(actual_duration_to_extract), "-an", "-c:v", "copy", temp_segment_path]
        
        process = run_command(ffmpeg_extract_cmd)
        if process.returncode != 0: raise Exception(trans_func("FFmpeg failed to extract scene: %s") % (process.stderr or process.stdout))
        
        call_if_open(player_instance, progress_dialog.Pulse, trans_func("Asking AI to analyze the scene... This may take a moment."))
        
        target_language_code = config_model.get_setting("application_language") or "en"
        target_language_name = lang_map.get(target_language_code.lower(), "English")

        user_focus_instruction = f'\nAdditionally, pay special attention to this user request: "{user_prompt}"' if user_prompt else ""

        scene_prompt = f"""
        Analyze the provided video clip to create an interactive auditory scene.
        Based on the complexity and number of distinct items, determine an appropriate grid size (e.g., 8x8 for simple scenes, 15x15 for complex ones).

        Your response must be a single JSON object with the following keys:
        1. "grid_size": An [width, height] array.
        2. "overall_description": A one-paragraph summary of the entire scene, its mood, and key elements.
        3. "objects": A JSON array where each object has:
            a. "label": A short descriptive name.
            b. "detailed_description": A full paragraph describing the object.
            c. "spatial_info": An object with "start_coord": an [x, y] array on your defined grid.
            d. "action_description": A sentence describing what the object is doing.

        **IMPORTANT: All textual descriptions MUST be in {target_language_name}.**
        {user_focus_instruction}
        """
        
        json_response_text, _ = audio_describer.get_json_response_from_gemini(temp_segment_path, scene_prompt)
        
        if json_response_text.startswith("```json"):
            json_response_text = json_response_text[7:-3].strip()
        
        scene_data = json.loads(json_response_text)

        call_if_open(player_instance, progress_dialog.Destroy)
        sound_player.play("aistop.mp3")
        call_if_open(player_instance, launch_explorer_in_thread, player_instance, scene_data, trans_func)
    except Exception as e:
        app_logger.error(f"Explore Scene Thread Error: {e}", exc_info=True)
        sound_player.play("error.mp3")
        call_if_open(player_instance, progress_dialog.Destroy)
        call_if_open(player_instance, wx.MessageBox, trans_func("Failed to analyze scene: %s") % e, trans_func("Error"), wx.OK | wx.ICON_ERROR, player_instance)
    finally:
        if temp_segment_path and os.path.exists(temp_segment_path):
            try: os.remove(temp_segment_path)
            except Exception: pass

def launch_explorer_in_thread(player_instance, scene_data, trans_func):
    def explorer_runner():
        try:
            explorer = SceneExplorer(scene_data, trans_func)
            explorer.run()
        except Exception as e:
            app_logger.error(f"An unexpected error occurred during Scene Explorer run: {e}", exc_info=True)
            wx.CallAfter(wx.MessageBox, trans_func("An unexpected error occurred in Scene Explorer: %s") % e, trans_func("Error"))
        finally:
            # THIS IS THE FIX: Re-initialize the mixer before showing the main window again.
            sound_player.reinitialize_mixer()
            wx.CallAfter(show_wx_windows, player_instance)

    if player_instance.parent_frame:
        player_instance.parent_frame.Hide()
    player_instance.Hide()
    
    pygame_thread = threading.Thread(target=explorer_runner, daemon=True)
    pygame_thread.start()

def show_wx_windows(player_instance):
    if player_instance and not player_instance.IsBeingDeleted():
        if player_instance.parent_frame:
            player_instance.parent_frame.Show()
        player_instance.Show()
        player_instance.Raise()
        player_instance.SetFocus()