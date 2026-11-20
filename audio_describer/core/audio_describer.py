# audio_describer/core/audio_describer.py
from ..i18n_setup import _
import os
import time
import json
import sys
import math

from .. import config
from ..utils.logger import app_logger
from ..models import config_model
from . import video_processor, timing
from . import gemini_helpers as gemini
from .gemini_helpers import GeminiAPIError, ContentBlockedError, TokenLimitError

# --- CONSTANTS ---
GLOSSARY_MAX_DURATION_SEC = 3000  # 50 minutes, typical limit for raw video processing
_UPLOAD_POLL_MAX_ATTEMPTS = 100
_UPLOAD_POLL_INTERVAL_SEC = 5

# --- PUBLIC API ---

def reset_gemini_client():
    """Proxy function to reset the Gemini client in the helper module."""
    gemini.reset_gemini_client()


def _upload_and_wait_for_active(client, video_path, status_callback=None):
    """Uploads a video to Gemini Files API and polls until it becomes ACTIVE.

    Returns the active file object. Raises GeminiAPIError on timeout or failure.
    """
    def _status(msg):
        if status_callback:
            status_callback(msg)
        app_logger.info(f"Upload: {msg}")

    _status(_("Uploading video '%s'...") % os.path.basename(video_path))
    # Work around httpx ASCII header encoding error when the file path
    # contains non-ASCII characters (e.g. Windows username with ö, ü, etc.)
    upload_path = video_path
    temp_copy = None
    try:
        os.fspath(video_path).encode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError):
        import tempfile
        import shutil
        suffix = os.path.splitext(video_path)[1]
        temp_copy = tempfile.NamedTemporaryFile(
            suffix=suffix, prefix="omni_upload_", delete=False
        )
        temp_copy.close()
        shutil.copy2(video_path, temp_copy.name)
        upload_path = temp_copy.name
        app_logger.info(f"Upload: copied to ASCII-safe temp path for upload")
    try:
        video_file_obj = client.files.upload(file=upload_path)
    finally:
        if temp_copy is not None:
            try:
                os.unlink(temp_copy.name)
            except OSError:
                pass
    _status(_("Video upload initiated: %s. Waiting for processing...") % video_file_obj.name)

    for attempt in range(1, _UPLOAD_POLL_MAX_ATTEMPTS + 1):
        if video_file_obj.state.name == "ACTIVE":
            _status(_("Video is ACTIVE."))
            return video_file_obj
        _status(_("Video processing (attempt %d)... state: %s") % (attempt, video_file_obj.state.name))
        time.sleep(_UPLOAD_POLL_INTERVAL_SEC)
        video_file_obj = client.files.get(name=video_file_obj.name)

    err_msg = _("Video processing timed out or failed. Final state: %s") % video_file_obj.state.name
    if getattr(video_file_obj, "error", None) and video_file_obj.error:
        err_msg += f" Server error: Code={getattr(video_file_obj.error, 'code', 'N/A')}, Message='{getattr(video_file_obj.error, 'message', 'N/A')}'"
    _status(err_msg)
    raise GeminiAPIError(err_msg)


def _build_video_part(video_file_obj, start_offset_sec=None, end_offset_sec=None):
    """Builds a video Part with optional videoMetadata for FPS and time offsets.

    Uses the Gemini API's native videoMetadata to control frame sampling rate
    and time clipping, avoiding the need for local FFmpeg re-encoding or splitting.
    """
    gemini._lazy_import_gemini_sdk()
    types = gemini.types

    target_fps = config_model.get_setting("frame_rate_for_ai")

    metadata_kwargs = {}
    if target_fps and target_fps > 0:
        metadata_kwargs["fps"] = target_fps
        app_logger.info(f"Setting Gemini videoMetadata fps={target_fps}")
    if start_offset_sec is not None:
        metadata_kwargs["start_offset"] = f"{start_offset_sec}s"
        app_logger.info(f"Setting Gemini videoMetadata start_offset={start_offset_sec}s")
    if end_offset_sec is not None:
        metadata_kwargs["end_offset"] = f"{end_offset_sec}s"
        app_logger.info(f"Setting Gemini videoMetadata end_offset={end_offset_sec}s")

    if metadata_kwargs:
        video_metadata = types.VideoMetadata(**metadata_kwargs)
        video_part = types.Part(
            file_data=types.FileData(
                file_uri=video_file_obj.uri,
                mime_type=video_file_obj.mime_type
            ),
            video_metadata=video_metadata
        )
        return video_part

    # No metadata needed - return raw file object (SDK handles conversion)
    return video_file_obj


def _cleanup_uploaded_file(client, video_file_obj, status_callback=None):
    """Safely deletes an uploaded file from Gemini."""
    if video_file_obj and hasattr(video_file_obj, 'name') and client:
        try:
            client.files.delete(name=video_file_obj.name)
            if status_callback:
                status_callback(_("Cleaned up uploaded video file: %s") % video_file_obj.name)
        except Exception as del_e:
            app_logger.error(f"Failed to delete file {video_file_obj.name}: {del_e}")


def generate_descriptions_and_glossary(video_path, user_prompt="", status_update_callback=None, initial_glossary=None):
    """
    Generates both audio descriptions and a character glossary in a single AI call.
    Uses Gemini's native videoMetadata for FPS control instead of local re-encoding.
    """
    def _update_status(msg):
        if status_update_callback:
            status_update_callback(msg)
        app_logger.info(f"Describer: {msg}")

    if not os.path.exists(video_path):
        _update_status(_("Error: Video file not found: %s") % video_path)
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_file_obj = None
    token_usage = {}
    client = None
    try:
        client = gemini.get_gemini_client()
        model_name_to_use = _get_model_name()
        video_file_obj = _upload_and_wait_for_active(client, video_path, _update_status)

        _update_status(_("Video ready. Preparing unified AI request..."))

        system_instruction, user_prompt_text = _build_unified_prompts(user_prompt, model_name_to_use, initial_glossary)

        gen_config = gemini.build_generation_config(system_instruction_text=system_instruction, is_json_response=True, enable_thinking=True)
        video_part = _build_video_part(video_file_obj)
        api_contents = [user_prompt_text, video_part]

        _update_status(_("Requesting descriptions and glossary from AI..."))
        response = gemini.generate_content_with_retry(
            client, model=model_name_to_use, contents=api_contents,
            config=gen_config, status_callback=_update_status
        )
        gemini.save_raw_ai_output(os.path.basename(video_path), "unified_raw_response", response)

        token_usage = gemini.log_token_usage("Unified", response)
        raw_json_text, success = gemini.process_gemini_response(response, _update_status)

        if not success or not raw_json_text:
            return [], [], token_usage

        descriptions, glossary = _parse_unified_response(raw_json_text, _update_status)
        if initial_glossary:  # characters reused from the previous video keep their ids and voices
            glossary = timing.merge_glossary([dict(g) for g in initial_glossary], glossary)

        if not descriptions:
            _update_status(_("Failed to parse timed descriptions from AI's JSON response."))

        if not glossary:
            _update_status(_("Failed to parse character glossary from AI's JSON response."))

        _update_status(_("Successfully parsed %d raw descriptions. Correcting timestamps and duplicates...") % len(descriptions))
        corrected_descriptions = _post_process_mmss_timestamps(descriptions, _update_status)
        final_descriptions = _remove_consecutive_duplicates(corrected_descriptions, _update_status)

        if not final_descriptions:
            _update_status(_("No descriptions remained after all post-processing."))

        app_logger.info(f"Unified generation complete. Descriptions: {len(final_descriptions)}, Glossary: {len(glossary)}")
        return final_descriptions, glossary, token_usage
    except Exception as e:
        _update_status(_("Unified generation failed: %s") % str(e))
        app_logger.error("Unified generation failed critically: %s", e, exc_info=True)
        raise
    finally:
        _cleanup_uploaded_file(client, video_file_obj, _update_status)

def generate_descriptions_chunked(video_path, chunk_duration_sec, user_prompt="", status_update_callback=None, initial_glossary=None):
    """Generates descriptions for a long video by uploading once and using time offsets.

    Instead of splitting the video into separate files and uploading each one,
    this uploads the full video once and uses Gemini's videoMetadata start/end
    offsets to process each chunk. This is significantly faster and more efficient.

    Returns: (all_descriptions, character_glossary, all_token_usage_list)
    """
    def _update_status(msg):
        if status_update_callback:
            status_update_callback(msg)
        app_logger.info(f"ChunkedDescriber: {msg}")

    if not os.path.exists(video_path):
        _update_status(_("Error: Video file not found: %s") % video_path)
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_file_obj = None
    client = None
    all_descriptions = []
    character_glossary = [dict(g) for g in (initial_glossary or [])]  # reused from the previous video
    all_token_usage = []

    try:
        client = gemini.get_gemini_client()
        model_name_to_use = _get_model_name()

        # Upload video once
        video_file_obj = _upload_and_wait_for_active(client, video_path, _update_status)

        # Determine chunk boundaries
        total_duration = video_processor.get_video_duration(video_path)
        if total_duration <= 0:
            raise ValueError(_("Could not determine video duration or video is empty."))

        num_chunks = math.ceil(total_duration / chunk_duration_sec)
        if num_chunks <= 1:
            # Video shorter than chunk size - process as single request
            _update_status(_("Video is shorter than chunk size, processing as single request."))
            num_chunks = 1

        _update_status(_("Processing video..."))

        enable_glossary = config_model.get_setting("enable_character_glossary")
        system_instruction, user_prompt_text = _build_unified_prompts(user_prompt, model_name_to_use, initial_glossary)
        gen_config = gemini.build_generation_config(
            system_instruction_text=system_instruction, is_json_response=True, enable_thinking=True
        )

        for i in range(num_chunks):
            chunk_start = i * chunk_duration_sec
            chunk_end = min((i + 1) * chunk_duration_sec, total_duration)

            _update_status(_("Processing chunk %d of %d (%.0fs - %.0fs)...") % (i + 1, num_chunks, chunk_start, chunk_end))

            video_part = _build_video_part(
                video_file_obj,
                start_offset_sec=chunk_start,
                end_offset_sec=chunk_end
            )
            api_contents = [user_prompt_text, video_part]

            response = gemini.generate_content_with_retry(
                client, model=model_name_to_use, contents=api_contents,
                config=gen_config, status_callback=_update_status
            )
            gemini.save_raw_ai_output(
                os.path.basename(video_path), "unified_raw_response",
                response, suffix=f"_chunk{i+1}"
            )

            chunk_usage = gemini.log_token_usage(f"Chunk_{i+1}", response)
            if chunk_usage:
                all_token_usage.append(chunk_usage)

            raw_json_text, success = gemini.process_gemini_response(response, _update_status)
            if not success or not raw_json_text:
                app_logger.warning(f"Chunk {i+1} returned no usable response.")
                continue

            chunk_descriptions, chunk_glossary = _parse_unified_response(raw_json_text, _update_status)

            if enable_glossary and chunk_glossary:
                timing.merge_glossary(character_glossary, chunk_glossary)

            if not chunk_descriptions:
                app_logger.warning(f"Chunk {i+1} returned no descriptions.")
                continue

            # Post-process chunk timestamps
            corrected_chunk = _post_process_mmss_timestamps(chunk_descriptions, _update_status)

            # Gemini returns ABSOLUTE timestamps (relative to the full video)
            # when using videoMetadata start/end offsets, so we detect whether
            # the timestamps are already absolute or chunk-relative and only
            # add chunk_start if they appear to be relative (i.e. near zero).
            if corrected_chunk:
                first_start = corrected_chunk[0][0]
                last_end = corrected_chunk[-1][1]
                # Heuristic: if the first timestamp is already >= chunk_start
                # (within a small tolerance), timestamps are absolute.
                timestamps_are_absolute = (
                    chunk_start > 0 and first_start >= chunk_start * 0.8
                )
                if timestamps_are_absolute:
                    app_logger.info(
                        f"Chunk {i+1}: timestamps appear absolute "
                        f"(first={first_start:.1f}s, chunk_start={chunk_start:.1f}s). "
                        f"Using as-is."
                    )
                    all_descriptions.extend(corrected_chunk)
                else:
                    app_logger.info(
                        f"Chunk {i+1}: timestamps appear chunk-relative "
                        f"(first={first_start:.1f}s, chunk_start={chunk_start:.1f}s). "
                        f"Adding offset {chunk_start:.1f}s."
                    )
                    for d in corrected_chunk:
                        all_descriptions.append(timing.Description(d[0] + chunk_start, d[1] + chunk_start, d[2], timing.flags_of(d)))

            _update_status(_("Chunk %d: parsed %d descriptions.") % (i + 1, len(corrected_chunk)))

        # Final deduplication across all chunks
        final_descriptions = _remove_consecutive_duplicates(all_descriptions, _update_status)

        if not final_descriptions:
            _update_status(_("No descriptions remained after all post-processing."))

        app_logger.info(f"Chunked generation complete. Descriptions: {len(final_descriptions)}, Glossary: {len(character_glossary)}")
        return final_descriptions, character_glossary, all_token_usage

    except Exception as e:
        _update_status(_("Chunked generation failed: %s") % str(e))
        app_logger.error("Chunked generation failed critically: %s", e, exc_info=True)
        raise
    finally:
        _cleanup_uploaded_file(client, video_file_obj, _update_status)


def ask_gemini_about_video_segment(video_segment_path, vqa_prompt_text, system_instruction_text_vqa=None):
    if not os.path.exists(video_segment_path):
        raise FileNotFoundError(f"Video segment file not found: {video_segment_path}")

    video_file_obj = None
    vqa_usage = {}
    client = None
    try:
        client = gemini.get_gemini_client()
        model_name_to_use = _get_model_name()

        video_file_obj = _upload_and_wait_for_active(client, video_segment_path)

        gen_config_obj = gemini.build_generation_config(is_json_response=False, enable_thinking=False)
        response = gemini.generate_content_with_retry(
            client, model=model_name_to_use, contents=[vqa_prompt_text, video_file_obj],
            config=gen_config_obj
        )

        gemini.save_raw_ai_output(os.path.basename(video_segment_path), "vqa_raw_response", response)
        vqa_usage = gemini.log_token_usage("VQA", response)
        answer, success = gemini.process_gemini_response(response, None)

        return (answer, vqa_usage) if success else (_("AI could not provide an answer."), vqa_usage)
    except Exception as e:
        app_logger.error("VQA failed critically: %s", e, exc_info=True)
        raise GeminiAPIError(_("VQA request failed: %s") % str(e))
    finally:
        _cleanup_uploaded_file(client, video_file_obj)


def get_json_response_from_gemini(video_path, prompt_text, status_update_callback=None, suffix=""):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    video_file_obj = None
    client = None
    try:
        client = gemini.get_gemini_client()
        model_name_to_use = _get_model_name()

        video_file_obj = _upload_and_wait_for_active(client, video_path, status_update_callback)

        gen_config_obj = gemini.build_generation_config(is_json_response=True, enable_thinking=False)
        response = gemini.generate_content_with_retry(
            client, model=model_name_to_use, contents=[prompt_text, video_file_obj],
            config=gen_config_obj, status_callback=status_update_callback
        )

        gemini.save_raw_ai_output(os.path.basename(video_path), "json_raw_response", response, suffix=suffix)

        if hasattr(response, 'candidates') and response.candidates and hasattr(response.candidates[0], 'finish_reason') and response.candidates[0].finish_reason.name == 'MAX_TOKENS':
            raise TokenLimitError(_("AI process stopped while generating glossary due to token limit. Try simplifying the video or adjusting settings."))

        usage_data = gemini.log_token_usage("JSON_Mode", response)
        json_text, success = gemini.process_gemini_response(response, status_update_callback)
        return (json_text, usage_data) if success else ("", usage_data)
    except Exception as e:
        app_logger.error("get_json_response_from_gemini failed: %s", e, exc_info=True)
        raise GeminiAPIError(_("Failed to get structured data from AI: %s") % str(e))
    finally:
        _cleanup_uploaded_file(client, video_file_obj)

# --- INTERNAL HELPERS ---

def _get_model_name():
    model_override = config_model.get_setting("gemini_model_override")
    return model_override if model_override and model_override.strip() else config.GEMINI_MODEL_NAME

def _mmss_to_total_seconds(mmss_string):
    if not isinstance(mmss_string, str) or mmss_string.count(':') != 1:
        if isinstance(mmss_string, str) and ':' not in mmss_string:
            try: return float(mmss_string)
            except ValueError: pass
        app_logger.warning("Invalid MM:SS string format for conversion: '%s'" % mmss_string)
        raise ValueError(_("Invalid MM:SS string format: %s") % mmss_string)
    parts = mmss_string.split(':', 1)
    try:
        minutes = int(parts[0])
        seconds_part_str = parts[1].replace(',', '.')
        sec_float = float(seconds_part_str)
        total_seconds = float(minutes * 60 + sec_float)
        if total_seconds < 0: raise ValueError(_("Calculated total seconds is negative."))
        return total_seconds
    except (ValueError, TypeError) as e:
        app_logger.error("Error parsing MM:SS components in '%s': %s" % (mmss_string, e))
        raise ValueError(_("Invalid MM:SS components in '%s': %s") % (mmss_string, e))

def _post_process_mmss_timestamps(descriptions_list_raw_times, status_update_callback=None):
    if not descriptions_list_raw_times: return []
    def _log_status_local(message):
        if status_update_callback: status_update_callback(message)
    corrected_descriptions = []; last_corrected_end_time_sec = 0.0; num_adjustments_made = 0
    for i, item in enumerate(descriptions_list_raw_times):
        start_mmss_str, end_mmss_str, text = item[:3]
        flags = item[3] if len(item) > 3 else ()  # translated dialogue: speaker gender and id
        made_adjustment_this_iteration = False
        try: current_start_sec = _mmss_to_total_seconds(start_mmss_str); current_end_sec = _mmss_to_total_seconds(end_mmss_str)
        except ValueError as e: _log_status_local(_("Skipping description due to invalid MM:SS format.")); continue
        adjusted_start_sec = current_start_sec; adjusted_end_sec = current_end_sec
        if corrected_descriptions and adjusted_start_sec < last_corrected_end_time_sec:
            adjusted_start_sec = last_corrected_end_time_sec + 0.001; made_adjustment_this_iteration = True
        if adjusted_end_sec <= adjusted_start_sec:
            original_duration_from_ai = current_end_sec - current_start_sec
            duration_to_add = max(0.1, original_duration_from_ai if original_duration_from_ai > 0 else 0.1)
            adjusted_end_sec = adjusted_start_sec + duration_to_add; _log_status_local(_("Ensuring minimum description duration...")); made_adjustment_this_iteration = True
        if made_adjustment_this_iteration: num_adjustments_made += 1
        corrected_descriptions.append(timing.Description(adjusted_start_sec, adjusted_end_sec, text, flags)); last_corrected_end_time_sec = adjusted_end_sec
    if status_update_callback: _log_status_local(_("Timestamp correction complete. %(count)d descriptions processed, %(adjusted_count)d had timestamps adjusted.") % {'count': len(corrected_descriptions), 'adjusted_count': num_adjustments_made})
    return corrected_descriptions

def _remove_consecutive_duplicates(descriptions_list, status_update_callback=None):
    if not descriptions_list: return []
    cleaned_list = [descriptions_list[0]]; duplicates_removed = 0
    for i in range(1, len(descriptions_list)):
        if descriptions_list[i][2].strip() != cleaned_list[-1][2].strip(): cleaned_list.append(descriptions_list[i])
        else: duplicates_removed += 1
    if duplicates_removed > 0 and status_update_callback: status_update_callback(_("Removed %(count)d repetitive descriptions.") % {'count': duplicates_removed})
    return cleaned_list

def _extract_descriptions_and_glossary_from_dict(data, status_update_callback):
    """Extracts descriptions and glossary from a parsed JSON dict."""
    descriptions_raw = data.get("audio_descriptions", [])
    descriptions = []
    if isinstance(descriptions_raw, list):
        for item in descriptions_raw:
            if isinstance(item, dict):
                start = item.get("start_time_mmss")
                end = item.get("end_time_mmss")
                text = item.get("description_text")
                if start is not None and end is not None and text is not None:
                    flags = timing.dialogue_flags(item.get("dialogue_translation") is True,
                                                  item.get("speaker_gender"), item.get("speaker_id"))
                    descriptions.append((str(start), str(end), str(text).strip(), flags))
    else:
        if status_update_callback:
            status_update_callback(_("Warning: 'audio_descriptions' key was not a list."))

    glossary = data.get("character_glossary", [])
    if not isinstance(glossary, list):
        if status_update_callback:
            status_update_callback(_("Warning: 'character_glossary' key was not a list."))
        glossary = []

    return descriptions, glossary


def _parse_unified_response(json_string, status_update_callback):
    """Parses the combined JSON response for descriptions and glossary."""
    if not json_string:
        return [], []

    processed_str = json_string.strip()
    if processed_str.startswith("```json"):
        processed_str = processed_str[7:].strip()
    if processed_str.endswith("```"):
        processed_str = processed_str[:-3].strip()

    try:
        data = json.loads(processed_str)
        return _extract_descriptions_and_glossary_from_dict(data, status_update_callback)
        
    except json.JSONDecodeError as e:
        status_update_callback(_("Error: Could not decode the AI's JSON response: %s") % str(e))
        app_logger.error(f"Failed to parse unified JSON response: {e}", exc_info=True)
        # Attempt to find a JSON object within the string if the initial parse fails
        try:
            start_idx = processed_str.find('{')
            end_idx = processed_str.rfind('}') + 1
            if 0 <= start_idx < end_idx:
                corrected_json_string = processed_str[start_idx:end_idx]
                data = json.loads(corrected_json_string)
                status_update_callback(_("Successfully parsed a fallback JSON object."))
                return _extract_descriptions_and_glossary_from_dict(data, status_update_callback)
        except json.JSONDecodeError:
            status_update_callback(_("Fallback JSON parsing also failed."))

    return [], []

def parse_gemini_json_response_mmss(json_string):
    if not json_string: return []
    processed_str = json_string.strip()
    if processed_str.startswith("```json"): processed_str = processed_str[7:].strip()
    if processed_str.endswith("```"): processed_str = processed_str[:-3].strip()
    start_index = processed_str.find('[')
    if start_index == -1: app_logger.error("Could not find start of JSON array '[' in the response."); return []
    processed_str = processed_str[start_index:]
    decoder = json.JSONDecoder(); descriptions_raw_times = []; pos = 0
    while pos < len(processed_str):
        obj_start = processed_str.find('{', pos)
        if obj_start == -1: break
        try:
            obj, end_pos = decoder.raw_decode(processed_str[obj_start:])
            if isinstance(obj, dict):
                start_mmss = obj.get("start_time_mmss"); end_mmss = obj.get("end_time_mmss"); desc_text = obj.get("description_text")
                if start_mmss is not None and end_mmss is not None and desc_text is not None:
                    descriptions_raw_times.append((str(start_mmss), str(end_mmss), str(desc_text).strip()))
            pos = obj_start + end_pos
        except json.JSONDecodeError: break
    return descriptions_raw_times


def _build_unified_prompts(user_prompt, model_name_to_use, initial_glossary=None):
    """Builds the system and user prompts for the unified generation API call."""
    target_language_code = config_model.get_setting("application_language") or "en"
    lang_map = {"en": "English", "es": "Spanish", "fr": "French", "ar": "Arabic", "pt": "Portuguese", "it": "Italian", "ru": "Russian", "uk": "Ukrainian", "vi": "vietnamese", "tr": "Turkish"}
    target_language_name = lang_map.get(target_language_code.lower(), "English")
    
    enable_glossary = config_model.get_setting("enable_character_glossary")
    
    # Get verbosity setting and convert to meaningful instruction
    verbosity_setting = config_model.get_setting('gemini_description_verbosity')
    verbosity_instructions = {
        config.VERBOSITY_SHORT: "Keep descriptions extremely brief (1-3 words maximum). Only describe the most critical visual elements that are essential for understanding the scene.",
        config.VERBOSITY_STANDARD: "Provide balanced descriptions (3-6 words). Focus on important visual information without overwhelming detail. This is the recommended setting.",
        config.VERBOSITY_DETAILED: "Provide rich, detailed descriptions (6-12 words). Include important visual context, emotions, scene details, and atmospheric elements that enhance understanding."
    }
    verbosity_instruction = verbosity_instructions.get(verbosity_setting, verbosity_instructions[config.VERBOSITY_STANDARD])
    app_logger.info(f"Using verbosity setting: {verbosity_setting} -> {verbosity_instruction[:50]}...")
    
    # Core directives remain largely the same, but the output format instruction is new.
    core_directives = """
**CORE DIRECTIVES (Apply to `audio_descriptions`):**
1.  **DO NOT OVERLAP DIALOGUE:** The most critical rule. Omit descriptions during dialogue unless it's a 1-3 word, critical, silent visual.
2.  **BE SELECTIVE AND CONCISE (2 WORDS/SECOND RULE):** Describe only NEW and PLOT-CRITICAL visual information. A 3-second description can have a maximum of 6 words.
3.  **USE NAMES ACCURATELY:** Use character names only after they are clearly revealed in the dialogue. Do not invent names.
4.  **Do not describe audible actions:** e.g., "a man talks". Describe new visual information.
"""

    # The main system instruction, now asking for a unified JSON object.
    system_instruction = f"""
You are an expert Audio Describer. Your mission is to analyze the provided video and generate two distinct sets of data in a single JSON object: a character glossary and a series of timed audio descriptions.

**OUTPUT FORMAT (Strict JSON):**
Your entire output MUST be a single JSON object with two top-level keys: "character_glossary" and "audio_descriptions".

1.  **"character_glossary":** An array of objects, where each object represents a distinct character. Each character object must contain:
    *   `"id"`: A unique, descriptive identifier (e.g., "man_in_red_shirt").
    *   `"description"`: A definitive physical description.
    *   `"name"`: The character's name, if and only if it is spoken clearly in the video. Otherwise, this must be null.

2.  **"audio_descriptions":** An array of objects, where each object represents a timed description. Each description object must contain:
    *   `"start_time_mmss"`: The start time of the description in "MM:SS" or "MM:SS.ms" format.
    *   `"end_time_mmss"`: The end time of the description in "MM:SS" or "MM:SS.ms" format.
    *   `"description_text"`: The concise description text, following all core directives.

{core_directives}

**EXAMPLE OUTPUT:**
{{
  "character_glossary": [
    {{"id": "man_in_suit", "description": "A tall man in a dark suit.", "name": "David"}}
  ],
  "audio_descriptions": [
    {{"start_time_mmss": "00:10.500", "end_time_mmss": "00:12.000", "description_text": "A car speeds down the street."}},
    {{"start_time_mmss": "00:15.250", "end_time_mmss": "00:17.000", "description_text": "David enters the room."}}
  ]
}}
"""

    user_prompt_parts = [
        f"Analyze the provided video and generate a unified JSON object containing {'the character glossary and ' if enable_glossary else ''}the timed audio descriptions. Follow all instructions.",
        "\n**Current Task Specifications:**",
        f"*   **Target Language for `description_text`:** {target_language_name}",
        f"*   **Verbosity Level:** {verbosity_instruction}",
    ]
    if config_model.get_setting("translate_foreign_dialogue"):
        user_prompt_parts.append(
            f"*   **Dialogue Translation (audio subtitles):** When someone speaks a language other than {target_language_name}, "
            f"add an `audio_descriptions` object for each line of that dialogue, timed with it, whose `description_text` is the "
            f"line translated into {target_language_name}, without a speaker name. These objects may overlap the dialogue they "
            "translate (an exception to directive 1) and also contain `\"dialogue_translation\": true`, "
            "`\"speaker_gender\"` (\"female\", \"male\" or \"unknown\", from the voice and appearance) and `\"speaker_id\"` "
            "(the speaker's `id` in `character_glossary`, or null). Give each glossary character an `\"apparent_gender\"` "
            "(\"female\", \"male\" or \"unknown\").")
    if initial_glossary:
        user_prompt_parts.append("*   **Known Characters:** reuse these exact ids, names and descriptions in "
                                 "`character_glossary` when these characters appear: "
                                 + json.dumps(initial_glossary, ensure_ascii=False))
    if user_prompt and user_prompt.strip():
        user_prompt_parts.append(f"*   **User's Specific Focus:** {user_prompt.strip()}")
        app_logger.info(f"Custom prompt applied: {user_prompt.strip()[:100]}...")
    else:
        app_logger.info("No custom prompt provided by user.")
        
    return system_instruction, "\n".join(user_prompt_parts)