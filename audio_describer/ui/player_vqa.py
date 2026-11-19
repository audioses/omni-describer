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
from . import call_if_open
from .scene_explorer_dialog import SceneExplorerDialog
from .explore_scene_options_dialog import ExploreSceneOptionsDialog


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
    
    dialog = player_instance.ask_more_dialog_instance
    if not (dialog and dialog.IsShown()):
        return
    history = list(dialog.exchanges[-6:])
    threading.Thread(target=_handle_ask_more_thread,
                     args=(player_instance, user_question, current_time_ms, context_duration_sec, media_total_duration_ms, _, history),
                     daemon=True).start()

def _handle_ask_more_thread(player_instance, user_question, segment_start_ms, context_duration_sec, media_total_duration_ms, trans_func, history=()):
    temp_segment_path = None
    ai_answer = trans_func("AI processing could not be completed.")
    vqa_usage_data = {} 
    is_error_response = True

    try:
        sound_player.play("aistart.mp3")
        # The question is about what the user just saw: clip the seconds leading up to the pause (+1 s), not after.
        now_sec = segment_start_ms / 1000.0
        end_sec = min(media_total_duration_ms / 1000.0, now_sec + 1.0)
        start_time_ffmpeg = max(0.0, end_sec - context_duration_sec)
        actual_duration_ffmpeg_sec = end_sec - start_time_ffmpeg
        if actual_duration_ffmpeg_sec <= 0: raise Exception(trans_func("No video context available."))

        target_language_name = audio_describer.description_language_name()
        context = audio_describer.story_context(getattr(player_instance, "descriptions", None),
                                                getattr(player_instance, "character_glossary", None), now_sec)
        vqa_prompt = audio_describer.build_vqa_prompt(user_question, target_language_name, history, context)
        kept = audio_describer.ask_about_kept_upload(
            player_instance.video_path, start_time_ffmpeg, start_time_ffmpeg + actual_duration_ffmpeg_sec, vqa_prompt)
        if kept is not None:
            ai_answer, vqa_usage_data = kept[0] or trans_func("AI could not provide an answer."), kept[1]
        else:
            temp_segment_path = os.path.join(tempfile.gettempdir(), f"ask_more_segment_{os.urandom(4).hex()}.mp4")
            ffmpeg_extract_cmd = [FFMPEG_COMMAND, "-y", "-ss", str(start_time_ffmpeg), "-i", player_instance.video_path, "-t", str(actual_duration_ffmpeg_sec), "-c:v", "copy", "-c:a", "copy", temp_segment_path]
            process_extract = run_command(ffmpeg_extract_cmd)
            if process_extract.returncode != 0: raise Exception(trans_func("FFmpeg failed to extract segment: %s") % (process_extract.stderr or process_extract.stdout))
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
            if getattr(player_instance, '_original_state_before_ask_more', None) and player_instance.vlc_player:
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

        call_if_open(player_instance, progress_dialog.Pulse, trans_func("Asking AI to analyze the scene... This may take a moment."))
        
        target_language_name = audio_describer.description_language_name()

        user_focus_instruction = f'\nAdditionally, pay special attention to this user request: "{user_prompt}"' if user_prompt else ""
        context = audio_describer.story_context(getattr(player_instance, "descriptions", None),
                                                getattr(player_instance, "character_glossary", None),
                                                segment_start_ms / 1000.0)
        if context:
            user_focus_instruction += ("\nName each person exactly as in the glossary and keep every object "
                                       "description consistent with this context:\n" + context)

        scene_prompt = f"""
        Analyze the provided video clip to create an interactive auditory scene.
        Based on the complexity and number of distinct items, determine an appropriate grid size (e.g., 8x8 for simple scenes, 15x15 for complex ones).

        Your response must be a single JSON object with the following keys:
        1. "grid_size": An [width, height] array.
        2. "overall_description": A one-paragraph summary of the entire scene, its mood, and key elements.
        3. "objects": A JSON array where each object has:
            a. "label": A short descriptive name.
            b. "detailed_description": A full paragraph describing the object. Start with its depth (foreground, middle ground or background) and its size compared with the frame or with a person.
            c. "spatial_info": An object with "start_coord": an [x, y] array on your defined grid.
            d. "action_description": A sentence describing what the object is doing.

        **IMPORTANT: All textual descriptions MUST be in {target_language_name}.**
        {user_focus_instruction}
        """
        
        kept = audio_describer.ask_about_kept_upload(
            player_instance.video_path, start_time_ffmpeg, start_time_ffmpeg + actual_duration_to_extract,
            scene_prompt, json_response=True)
        if kept is not None:
            json_response_text = kept[0]
        else:
            temp_segment_path = os.path.join(tempfile.gettempdir(), f"explore_scene_{os.urandom(4).hex()}.mp4")
            ffmpeg_extract_cmd = [FFMPEG_COMMAND, "-y", "-ss", str(start_time_ffmpeg), "-i", player_instance.video_path, "-t", str(actual_duration_to_extract), "-an", "-c:v", "copy", temp_segment_path]
            process = run_command(ffmpeg_extract_cmd)
            if process.returncode != 0: raise Exception(trans_func("FFmpeg failed to extract scene: %s") % (process.stderr or process.stdout))
            json_response_text, _ = audio_describer.get_json_response_from_gemini(temp_segment_path, scene_prompt)

        if json_response_text.startswith("```json"):
            json_response_text = json_response_text[7:-3].strip()
        
        scene_data = json.loads(json_response_text)

        call_if_open(player_instance, progress_dialog.Destroy)
        sound_player.play("aistop.mp3")
        call_if_open(player_instance, launch_explorer, player_instance, scene_data)
    except Exception as e:
        app_logger.error(f"Explore Scene Thread Error: {e}", exc_info=True)
        sound_player.play("error.mp3")
        call_if_open(player_instance, progress_dialog.Destroy)
        call_if_open(player_instance, wx.MessageBox, trans_func("Failed to analyze scene: %s") % e, trans_func("Error"), wx.OK | wx.ICON_ERROR, player_instance)
    finally:
        if temp_segment_path and os.path.exists(temp_segment_path):
            try: os.remove(temp_segment_path)
            except Exception: pass

def launch_explorer(player_instance, scene_data):
    if not player_instance or player_instance.IsBeingDeleted():
        return
    with SceneExplorerDialog(player_instance, scene_data) as dlg:
        dlg.ShowModal()
    player_instance.SetFocus()
