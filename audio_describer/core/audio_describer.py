# audio_describer/core/audio_describer.py
from ..i18n_setup import _
import os
import time
import json
import sys
import math
import atexit
import threading

from .. import app_state, config
from ..utils.logger import app_logger
from ..models import config_model
from . import video_processor
from . import gemini_helpers as gemini
from . import timing
from .gemini_helpers import GeminiAPIError, ContentBlockedError, TokenLimitError

# --- CONSTANTS ---
GLOSSARY_MAX_DURATION_SEC = 3000  # 50 minutes, typical limit for raw video processing
_UPLOAD_POLL_MAX_ATTEMPTS = 100
_UPLOAD_POLL_INTERVAL_SEC = 5

# --- PUBLIC API ---

GenerationCancelled = app_state.GenerationCancelled  # one class, raised from any layer


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise GenerationCancelled(_("Generation cancelled by user."))


def reset_gemini_client():
    """Proxy function to reset the Gemini client in the helper module."""
    gemini.reset_gemini_client()


def _upload_and_wait_for_active(client, video_path, status_callback=None, cancel_event=None):
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
        video_file_obj = app_state.run_cancellable(client.files.upload, file=upload_path)
    finally:
        if temp_copy is not None:
            try:
                os.unlink(temp_copy.name)
            except OSError:
                pass
    _status(_("Video upload initiated: %s. Waiting for processing...") % video_file_obj.name)

    for attempt in range(1, _UPLOAD_POLL_MAX_ATTEMPTS + 1):
        _check_cancel(cancel_event)
        if video_file_obj.state.name == "ACTIVE":
            _status(_("Video is ACTIVE."))
            return video_file_obj
        _status(_("Video processing (attempt %d)... state: %s") % (attempt, video_file_obj.state.name))
        if cancel_event is not None:
            cancel_event.wait(_UPLOAD_POLL_INTERVAL_SEC)
            _check_cancel(cancel_event)
        else:
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


# Audit §5.3 #5: the last video generation uploaded is kept so Ask More and
# Explore Scene ask about it with videoMetadata offsets (frame-exact, no
# ffmpeg cut, no re-upload). Only one is kept, so batch runs can't pile up
# against the Files API quota. Deleted at exit; Gemini drops files after 48 h anyway.
_kept_upload = None  # (normalised video path, file object)


def _upload_key(path):
    return os.path.normcase(os.path.abspath(path))


def _keep_upload(client, video_path, video_file_obj):
    global _kept_upload
    previous, _kept_upload = _kept_upload, (_upload_key(video_path), video_file_obj)
    if previous is not None:
        _cleanup_uploaded_file(client, previous[1])


def release_kept_upload():
    """Delete the kept upload. Bounded wait so closing the app offline doesn't hang."""
    global _kept_upload
    kept, _kept_upload = _kept_upload, None
    if kept is None:
        return
    def _delete():
        try:
            _cleanup_uploaded_file(gemini.get_gemini_client(), kept[1])
        except Exception as e:
            app_logger.warning(f"Could not delete kept upload: {e}")
    t = threading.Thread(target=_delete, daemon=True)
    t.start()
    t.join(timeout=5)


atexit.register(release_kept_upload)


def _require_gemini_backend():
    """Gemini-only features (questions, scene explorer, transcript, audio introduction) refuse to run
    when the user picked another vision backend, so nothing is sent to Google behind their back."""
    if (config_model.get_setting("video_backend") or "gemini") != "gemini":
        raise GeminiAPIError(_("This feature uses Gemini, but video descriptions are set to another AI in "
                               "Settings, so nothing was sent to Google. Switch to Gemini in Settings to use it."))


def ask_about_kept_upload(video_path, start_sec, end_sec, prompt_text, json_response=False):
    """Ask about [start_sec, end_sec] of the video generation already uploaded.

    Returns (text, usage), or None when there is no live upload for this file
    (another video, expired, API key changed); the caller then sends a clip.
    """
    global _kept_upload
    _require_gemini_backend()
    kept = _kept_upload
    if kept is None or kept[0] != _upload_key(video_path):
        return None
    client = gemini.get_gemini_client()
    try:
        video_file_obj = client.files.get(name=kept[1].name)
        if video_file_obj.state.name != "ACTIVE":
            raise ValueError(video_file_obj.state.name)
    except Exception as e:
        app_logger.info(f"Kept upload no longer usable ({e}); falling back to a clip.")
        _kept_upload = None
        return None
    config_obj = gemini.build_generation_config(is_json_response=json_response, enable_thinking=False)
    response = gemini.generate_content_with_retry(
        client, model=_get_model_name(),
        contents=[prompt_text, _build_video_part(video_file_obj, start_sec, end_sec)], config=config_obj)
    gemini.save_raw_ai_output(os.path.basename(video_path), "kept_upload_response", response)
    usage = gemini.log_token_usage("KeptUpload", response)
    text, success = gemini.process_gemini_response(response, None)
    return (text if success else ""), usage


# JSON schema enforced by the API (structured output): no markdown fences, no
# free-form parsing. Field names kept for compatibility with saved raw outputs.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "character_glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "apparent_gender": {"type": "string", "enum": ["female", "male", "unknown"]},
                },
                "required": ["id", "description"],
            },
        },
        "content_profile": {
            "type": "object",
            "properties": {
                "genre": {"type": "string"},
                "audience": {"type": "string"},
                "spoken_language": {"type": "string"},
            },
        },
        "audio_descriptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_time_mmss": {"type": "string"},
                    "end_time_mmss": {"type": "string"},
                    "description_text": {"type": "string"},
                    "level": {"type": "integer", "enum": [1, 2, 3]},
                    "on_screen_text": {"type": "boolean"},
                    "dialogue_translation": {"type": "boolean"},
                    "speaker_gender": {"type": "string", "enum": ["female", "male", "unknown"]},
                    "speaker_id": {"type": "string"},
                },
                "required": ["start_time_mmss", "end_time_mmss", "description_text", "level"],
            },
        },
    },
    "required": ["character_glossary", "audio_descriptions"],
}



def generate_descriptions_and_glossary(video_path, user_prompt="", status_update_callback=None, cancel_event=None, audio_source_path=None, initial_glossary=None):
    """Whole video in one request (a single 'chunk'). Returns (descriptions, glossary, token_usage_list)."""
    return generate_descriptions_chunked(video_path, None, user_prompt, status_update_callback, cancel_event, audio_source_path, initial_glossary)


def generate_descriptions_chunked(video_path, chunk_duration_sec, user_prompt="", status_update_callback=None, cancel_event=None, audio_source_path=None, initial_glossary=None):
    """Upload once, then describe the video chunk by chunk with videoMetadata offsets.

    Pipeline (ADLAB 5 steps): local pass finds quiet windows in the soundtrack
    (ffmpeg silencedetect), the model writes level-tagged descriptions for those
    windows with a word budget, each chunk gets the glossary and last
    descriptions of the previous ones (MM-Narrator-style memory), then every
    description is checked against its window and flagged.

    chunk_duration_sec=None means a single request for the whole video.
    Returns: (descriptions as timing.Description, character_glossary, token_usage_list)
    """
    def _update_status(msg):
        if status_update_callback:
            status_update_callback(msg)
        app_logger.info(f"Describer: {msg}")

    if not os.path.exists(video_path):
        _update_status(_("Error: Video file not found: %s") % video_path)
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_file_obj = None
    client = None
    keep_upload = False
    all_descriptions = []
    character_glossary = [dict(g) for g in (initial_glossary or [])]  # series: earlier episode's characters
    all_token_usage = []

    try:
        client = gemini.get_gemini_client()
        model_name_to_use = _get_model_name()
        total_duration = video_processor.get_video_duration(video_path)
        started = time.monotonic()
        first_ready = None
        if total_duration > 0:  # audit §6.3: say the size of the request before sending it
            tokens = timing.estimate_video_tokens(total_duration, config_model.get_setting("frame_rate_for_ai"),
                                                  model_name_to_use,
                                                  with_audio=not config_model.get_setting("send_silenced_video_to_ai"))
            _update_status(_("About %(tokens)s tokens will be sent to %(model)s for %(duration)s of video. Press Escape to cancel.")
                           % {"tokens": f"{tokens:,}", "model": model_name_to_use, "duration": timing.format_hms(total_duration)})
        # Step 2-3 (ADLAB): find the pauses locally, for free, before paying for tokens.
        quiet_windows = shot_changes = None
        if config_model.get_setting("enable_silence_detection") and total_duration > 0:
            _update_status(_("Detecting pauses in the soundtrack..."))
            quiet_windows = video_processor.detect_quiet_windows(audio_source_path or video_path, total_duration)
            if quiet_windows is not None:
                _update_status(_("Found %d pauses in the soundtrack.") % len(quiet_windows))
            _check_cancel(cancel_event)
            _update_status(_("Detecting shot changes..."))
            shot_changes = video_processor.detect_shot_changes(video_path)
            _check_cancel(cancel_event)

        video_file_obj = _upload_and_wait_for_active(client, video_path, _update_status, cancel_event)
        _check_cancel(cancel_event)

        if not chunk_duration_sec or chunk_duration_sec <= 0 or total_duration <= 0:
            num_chunks, chunk_duration_sec = 1, (total_duration if total_duration > 0 else None)
        else:
            num_chunks = max(1, math.ceil(total_duration / chunk_duration_sec))

        for i in range(num_chunks):
            _check_cancel(cancel_event)
            single = num_chunks == 1
            chunk_start = 0.0 if single else i * chunk_duration_sec
            chunk_end = total_duration if (single or total_duration <= 0) else min((i + 1) * chunk_duration_sec, total_duration)

            if single:
                _update_status(_("Requesting descriptions from AI..."))
            else:
                _update_status(_("Chunk %(n)d of %(total)d, %(pct)d%% done.") % {'n': i + 1, 'total': num_chunks, 'pct': int(100 * i / num_chunks)})

            chunk_windows = None
            if quiet_windows is not None and chunk_end:
                chunk_windows = [(a, b) for a, b in quiet_windows if b > chunk_start and a < chunk_end]

            chunk_cuts = shot_changes
            if shot_changes and chunk_end:
                chunk_cuts = [t for t in shot_changes if chunk_start <= t < chunk_end]
            context = _chunk_context(character_glossary, all_descriptions)
            system_instruction, user_prompt_text = _build_unified_prompts(
                user_prompt, model_name_to_use, quiet_windows=chunk_windows, context=context, shot_changes=chunk_cuts)
            gen_config = gemini.build_generation_config(
                system_instruction_text=system_instruction, is_json_response=True, enable_thinking=True,
                response_schema=RESPONSE_SCHEMA, model_name=model_name_to_use)

            if single:
                video_part = _build_video_part(video_file_obj)
            else:
                video_part = _build_video_part(video_file_obj, start_offset_sec=chunk_start, end_offset_sec=chunk_end)

            response = gemini.generate_content_with_retry(
                client, model=model_name_to_use, contents=[user_prompt_text, video_part],
                config=gen_config, status_callback=_update_status
            )
            _check_cancel(cancel_event)
            gemini.save_raw_ai_output(os.path.basename(video_path), "unified_raw_response", response,
                                      suffix="" if single else f"_chunk{i+1}")

            chunk_usage = gemini.log_token_usage("Unified" if single else f"Chunk_{i+1}", response)
            if chunk_usage:
                all_token_usage.append(chunk_usage)

            raw_json_text, success = gemini.process_gemini_response(response, _update_status)
            if not success or not raw_json_text:
                app_logger.warning(f"Chunk {i+1} returned no usable response.")
                continue

            chunk_descriptions, chunk_glossary = _parse_unified_response(raw_json_text, _update_status)
            if i == 0:
                _announce_content_profile(raw_json_text, _update_status)
            if chunk_glossary:  # always requested by the schema; dropping it left names inconsistent
                _merge_glossary(character_glossary, chunk_glossary)
            if not chunk_descriptions:
                app_logger.warning(f"Chunk {i+1} returned no descriptions.")
                continue

            corrected_chunk = _post_process_mmss_timestamps(chunk_descriptions, _update_status)

            # B7: Gemini may answer with absolute or clip-relative times when
            # videoMetadata offsets are used; detect and shift if relative.
            # Absolute times all fall at or after chunk_start; judge on the median, not the first
            # item (a late first description in chunk 2 used to be taken as absolute: 600 s early).
            offset = 0.0
            if corrected_chunk and chunk_start > 0:
                starts = sorted(d[0] for d in corrected_chunk)
                if starts[len(starts) // 2] < chunk_start - 1.0:
                    offset = chunk_start
            app_logger.info(f"Chunk {i+1}: applying offset {offset:.1f}s (chunk_start={chunk_start:.1f}s).")
            for d in corrected_chunk:
                all_descriptions.append(timing.Description(d[0] + offset, d[1] + offset, d[2], d.level, d.flags))
            if first_ready is None and corrected_chunk:
                first_ready = time.monotonic() - started  # audit §9: time to the first description
                app_logger.info(f"METRIC time_to_first_description={first_ready:.1f}s")

            if not single:
                _update_status(_("Chunk %d: parsed %d descriptions.") % (i + 1, len(corrected_chunk)))

        all_descriptions.sort(key=lambda d: d[0])
        final_descriptions = _remove_consecutive_duplicates(all_descriptions, _update_status)
        final_descriptions = [
            timing.Description(d[0], d[1], d[2], d.level,
                               timing.compute_flags(d[0], d[1], d[2], quiet_windows,
                                                    on_screen_text=timing.FLAG_ON_SCREEN_TEXT in d.flags,
                                                    translation=timing.FLAG_TRANSLATION in d.flags,
                                                    gender=timing.line_gender(d)) + timing.speaker_flags(d))
            for d in final_descriptions
        ]
        final_descriptions, _cast = timing.apply_voice_casting(final_descriptions, character_glossary)
        if final_descriptions and config_model.get_setting("enable_quality_check"):
            _check_cancel(cancel_event)
            final_descriptions = _review_script(client, model_name_to_use, final_descriptions, character_glossary, _update_status)
        flagged = sum(1 for d in final_descriptions if {timing.FLAG_OVER_BUDGET, timing.FLAG_OVERLAPS_SOUND, timing.FLAG_UNCERTAIN} & set(d.flags))
        _update_status(_("%(count)d descriptions ready, %(flagged)d to review.") % {'count': len(final_descriptions), 'flagged': flagged})

        if not final_descriptions:
            _update_status(_("No descriptions remained after all post-processing."))

        app_logger.info(f"Generation complete. Descriptions: {len(final_descriptions)}, Glossary: {len(character_glossary)}")
        keep_upload = True
        return final_descriptions, character_glossary, all_token_usage

    except GenerationCancelled:
        raise
    except Exception as e:
        _update_status(_("Generation failed: %s") % str(e))
        app_logger.error("Generation failed critically: %s", e, exc_info=True)
        raise
    finally:
        if keep_upload and video_file_obj is not None:
            # Keyed by the original file the player opens: preprocessing only strips audio, same timeline.
            _keep_upload(client, audio_source_path or video_path, video_file_obj)
        else:
            _cleanup_uploaded_file(client, video_file_obj, _update_status)


def _text_json_call(client, model_name, system_instruction, prompt, schema, context_label):
    """Cheap text-only request (no video) with a structured JSON answer."""
    config = gemini.build_generation_config(system_instruction_text=system_instruction, is_json_response=True,
                                            enable_thinking=False, response_schema=schema, model_name=model_name)
    response = gemini.generate_content_with_retry(client, model=model_name, contents=[prompt], config=config)
    gemini.log_token_usage(context_label, response)
    raw, ok = gemini.process_gemini_response(response, None)
    return json.loads(raw) if ok and raw else {}


def _numbered_script(descriptions):
    return "\n".join(f"[{i}] {timing.format_vtt_time(d[0])} (level {timing.level_of(d)}): {d[2]}"
                     for i, d in enumerate(descriptions))


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"},
                       "kind": {"type": "string", "enum": ["error", "past_tense", "interpretation", "out_of_fiction"]},
                       "reason": {"type": "string"}},
        "required": ["index", "kind", "reason"]}}},
    "required": ["issues"],
}


def _review_script(client, model_name, descriptions, glossary, update_status):
    """Second, text-only pass (audit §7.2.6): flag likely hallucinations and inconsistencies
    as 'uncertain' so the user can check them with Ask More. Never blocks generation."""
    update_status(_("Checking the descriptions for inconsistencies..."))
    system = ("You are a senior audio description quality reviewer. You only see the script, not the video. "
              "Report each problem with its kind:\n"
              "- error: probably wrong: a character name or descriptor that contradicts the glossary or earlier "
              "descriptions, a name used before it could be known, an object or person that contradicts its "
              "surroundings, impossible sequences, grammar, spelling or agreement mistakes in the script's language;\n"
              "- past_tense: not written in the present tense (past perfect referring back to earlier events is fine);\n"
              "- interpretation: states a feeling or intention instead of describing what is seen "
              "(\"he seems sad\" instead of \"he lowers his eyes\"), gives a personal opinion, or judges looks "
              "(\"beautiful\"); naming an obvious, visible emotion such as a smile is fine;\n"
              "- out_of_fiction: leaves the story: actors' names, camera movements, shots, editing or filming jargon "
              "(a technique that carries meaning, such as \"In slow motion,\", is fine).\n"
              "On-screen text read aloud and translated dialogue are quotations: do not report their tense or content. "
              "Report nothing if the script is fine.")
    if config_model.get_setting("description_style") == "subjective":
        system += " The user chose a subjective, evocative style: never report the interpretation kind."
    prompt = ("Glossary:\n" + json.dumps(glossary, ensure_ascii=False) + "\n\nScript:\n" + _numbered_script(descriptions))
    try:
        issues = _text_json_call(client, model_name, system, prompt, REVIEW_SCHEMA, "QualityReview").get("issues") or []
    except Exception as e:
        app_logger.warning(f"Quality review skipped: {e}")
        return descriptions
    kind_flags = {"error": timing.FLAG_UNCERTAIN, "past_tense": timing.FLAG_PAST_TENSE,
                  "interpretation": timing.FLAG_INTERPRETATION, "out_of_fiction": timing.FLAG_OUT_OF_FICTION}
    marked = {}  # index -> new flags
    for issue in issues:
        idx, flag = issue.get("index"), kind_flags.get(issue.get("kind"), timing.FLAG_UNCERTAIN)
        if isinstance(idx, int) and 0 <= idx < len(descriptions):
            if flag in timing.STYLE_FLAGS and timing.is_read_text(descriptions[idx]):
                continue  # quotations keep their own tense and wording
            marked.setdefault(idx, []).append(flag)
            app_logger.info(f"Quality review: #{idx} {flag}: {issue.get('reason')}")
    out = [timing.Description(d[0], d[1], d[2], timing.level_of(d),
                              tuple(timing.flags_of(d)) + tuple(f for f in dict.fromkeys(marked.get(i, ()))
                                                                if f not in timing.flags_of(d)))
           for i, d in enumerate(descriptions)]
    uncertain = sum(1 for flags in marked.values() if timing.FLAG_UNCERTAIN in flags)
    style = sum(1 for flags in marked.values() if set(flags) & set(timing.STYLE_FLAGS))
    update_status(_("Quality check: %(uncertain)d descriptions uncertain, %(style)d with style issues.")
                  % {"uncertain": uncertain, "style": style})
    return out


DIALOGUE_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {
        "type": "object",
        "properties": {"start_time_mmss": {"type": "string"}, "speaker": {"type": "string"}, "text": {"type": "string"}},
        "required": ["start_time_mmss", "speaker", "text"]}}},
    "required": ["lines"],
}


def transcribe_dialogue(video_path):
    """Dialogue transcript for the full script (WCAG 1.2.8): [(start_seconds, speaker, text), ...].
    Speakers use the names or descriptors heard or seen; songs and important sounds in brackets."""
    _require_gemini_backend()
    client = gemini.get_gemini_client()
    model_name = _get_model_name()
    uploaded = None
    try:
        uploaded = _upload_and_wait_for_active(client, video_path)
        prompt = ("Transcribe every line of dialogue and narration in this video, in the original language, with its start "
                  "time (MM:SS.mmm, HH:MM:SS.mmm past one hour) and the speaker: their name if it is said or shown, "
                  "otherwise a short stable descriptor (\"woman in red\"). Add important sounds and song lyrics as lines "
                  "with speaker \"[sound]\" or \"[song]\". Write speaker descriptors in " + description_language_name() + ".")
        config = gemini.build_generation_config(is_json_response=True, enable_thinking=False,
                                                response_schema=DIALOGUE_SCHEMA, model_name=model_name)
        response = gemini.generate_content_with_retry(client, model=model_name, contents=[prompt, uploaded], config=config)
        gemini.log_token_usage("DialogueTranscript", response)
        text, ok = gemini.process_gemini_response(response, None)
        if not ok or not text:
            raise GeminiAPIError(_("AI could not provide an answer."))
        lines = []
        for item in json.loads(text).get("lines") or []:
            try:
                seconds = timing.parse_timestamp(item.get("start_time_mmss", ""))
            except ValueError:
                continue
            if (item.get("text") or "").strip():
                lines.append((seconds, (item.get("speaker") or "").strip(), item["text"].strip()))
        return lines
    finally:
        _cleanup_uploaded_file(client, uploaded)


INTRO_SCHEMA = {"type": "object", "properties": {"introduction": {"type": "string"}}, "required": ["introduction"]}


def generate_audio_introduction(descriptions, glossary, title="", duration_sec=0):
    """Audio introduction (Ofcom §4.14, ADLAB §3.2): main characters, look and places, heard before the
    programme. Text-only request built from the existing script, so no new video upload."""
    _require_gemini_backend()
    client = gemini.get_gemini_client()
    model_name = _get_model_name()
    system = ("You write audio introductions for blind and partially sighted audiences, heard before a film or "
              "episode. Open with a short welcome, the title and the running time if given. Set up the premise "
              "together with the main characters (name or stable descriptor, appearance, costume, described with "
              "the same care for every character), then the main places and the visual style if notable. End by "
              "saying how unnamed characters are referred to in the description (e.g. 'the bearded man'). "
              "About 200 to 300 words of flowing prose, present tense. Never reveal plot events, twists or who "
              "does what later; no spoilers.")
    prompt = (f"Language: {description_language_name()}\n"
              + (f"Title (file name, tidy it up): {title}\n" if title else "")
              + (f"Running time: {timing.format_hms(duration_sec)}\n" if duration_sec > 0 else "")
              + "\nCharacter glossary:\n" + json.dumps(glossary, ensure_ascii=False)
              + "\n\nAudio description script:\n" + _numbered_script(descriptions))
    return (_text_json_call(client, model_name, system, prompt, INTRO_SCHEMA, "AudioIntroduction").get("introduction") or "").strip()


def _announce_content_profile(raw_json_text, update_status):
    """Tell the user what the AI recognised (audit §10.10); a preset or focus overrides it next time."""
    try:
        profile = json.loads(raw_json_text).get("content_profile") or {}
    except (ValueError, AttributeError):
        return
    if any(profile.get(k) for k in ("genre", "audience", "spoken_language")):
        update_status(_("Detected content: %(genre)s, for %(audience)s, spoken language %(lang)s. Choose a preset or a focus to change it.") % {
            'genre': profile.get("genre") or "?", 'audience': profile.get("audience") or "?",
            'lang': profile.get("spoken_language") or "?"})


def _chunk_context(glossary, descriptions, last_n=5):
    """Memory passed to the next chunk so names and descriptors stay consistent."""
    lines = []
    if glossary:
        lines.append("Known characters (reuse these names/descriptors exactly):")
        for g in glossary[:40]:
            name = g.get("name") or "name not revealed yet"
            lines.append(f"- {g.get('id')} ({name}): {g.get('description', '')}")
    if descriptions:
        lines.append("Last descriptions before this part (do not repeat or refer to them):")
        lines += [f"- {d[2]}" for d in descriptions[-last_n:]]
    return "\n".join(lines) if lines else None


HEARTBEAT_SEC = 25  # audit §9: never more than 30 s without news during generation


class Heartbeat:
    """Wraps the status callback: says "still working" after HEARTBEAT_SEC of silence
    and measures the longest silence (logged as an audit §9 indicator)."""
    def __init__(self, status, interval=HEARTBEAT_SEC):
        import threading
        self._status, self._interval = status, interval
        self.start = self.last = time.monotonic()
        self.longest = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def __call__(self, msg):
        with self._lock:
            now = time.monotonic()
            self.longest = max(self.longest, now - self.last)
            self.last = now
        self._status(msg)

    def _run(self):
        while not self._stop.wait(1):
            if time.monotonic() - self.last >= self._interval:
                self(_("Still working, %s elapsed.") % timing.format_hms(time.monotonic() - self.start))

    def close(self):
        self._stop.set()
        self.longest = max(self.longest, time.monotonic() - self.last)
        app_logger.info(f"METRIC longest_silence={self.longest:.1f}s total={time.monotonic() - self.start:.1f}s")


def describe_video(video_path, user_prompt="", status_update_callback=None, cancel_event=None, initial_glossary=None):
    """Whole pipeline without any UI (used by the main window and the command line):
    pre-process for the AI, generate (chunked or not), always clean up.
    Returns (descriptions, glossary, token_usage)."""
    status = Heartbeat(status_update_callback or (lambda msg: app_logger.info(msg)))
    app_state.set_cancel_event(cancel_event)  # lets ffmpeg, retries and HTTP calls below stop on Escape
    try:
        processed, was_preprocessed = None, False
        if config_model.get_setting("video_backend") in ("ollama", "openai"):  # local frames: no upload, no preprocessing
            from . import ollama_video
            return ollama_video.describe_video_locally(video_path, user_prompt, status, cancel_event, initial_glossary)
        processed, was_preprocessed = video_processor.preprocess_video_for_ai(video_path, status)
        video_for_ai = processed if was_preprocessed else video_path
        _check_cancel(cancel_event)
        if config_model.get_setting("enable_video_chunking"):
            return generate_descriptions_chunked(
                video_for_ai, config_model.get_setting("video_chunk_duration_seconds"), user_prompt, status,
                cancel_event, audio_source_path=video_path, initial_glossary=initial_glossary)
        return generate_descriptions_and_glossary(
            video_for_ai, user_prompt, status, cancel_event, audio_source_path=video_path,
            initial_glossary=initial_glossary)
    finally:
        app_state.set_cancel_event(None)
        status.close()
        if was_preprocessed and processed and os.path.exists(processed):
            video_processor.cleanup_temp_file(processed)


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic")
DOCUMENT_EXTENSIONS = (".pdf",)


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"  # local only: nothing leaves the computer


def _describe_with_ollama(path, prompt):
    """Privacy / offline mode (audit P3): a local vision model through Ollama. Images only."""
    import base64
    import urllib.request
    import urllib.error
    with open(path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    model = config_model.get_setting("ollama_model") or "gemma3"
    body = json.dumps({"model": model, "prompt": prompt, "images": [image_b64], "stream": False}).encode("utf-8")
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise GeminiAPIError(_("Ollama refused the request (model '%(model)s'): %(detail)s") % {"model": model, "detail": detail})
    except (urllib.error.URLError, OSError) as e:
        raise GeminiAPIError(_("Ollama is not running on this computer. Install it from ollama.com, then run: ollama pull %s") % model) from e
    text = (answer.get("response") or "").strip()
    if not text:
        raise GeminiAPIError(_("AI could not provide an answer."))
    return text


def _describe_with_openai_compatible(path, prompt):
    """An image through the OpenAI-compatible vision service, re-encoded as JPEG (the one format all accept)."""
    import base64
    from . import openai_vision
    jpeg = video_processor.image_as_jpeg_bytes(path)
    if jpeg is None:
        raise GeminiAPIError(_("Could not read this image. FFmpeg is required to send it to the vision service."))
    text = openai_vision.chat(None, prompt, [base64.b64encode(jpeg).decode("ascii")]).strip()
    if not text:
        raise GeminiAPIError(_("AI could not provide an answer."))
    return text


def describe_file(path, question=""):
    """Audio-description style text for a picture, a screenshot or a PDF (audit P3)."""
    is_document = path.lower().endswith(DOCUMENT_EXTENSIONS)
    backend = config_model.get_setting("image_backend")
    local = backend in ("ollama", "openai")
    if local and is_document:
        raise GeminiAPIError(_("This vision model can only describe images; switch to Gemini in Settings for PDF documents."))
    client = None if local else gemini.get_gemini_client()
    uploaded = None
    try:
        if is_document:
            prompt = ("Describe this document for a blind reader: its type and layout, then the images, charts, "
                      "tables and diagrams (with their key values), then summarise the text by section. "
                      "Read headings exactly.")
        else:
            prompt = ("Describe this image for a blind person, most important first: what it is (photo, screenshot, "
                      "drawing, painting...), the main subject, then the setting, people (appearance, expression, clothing), "
                      "notable colours, and read any visible text exactly. Say the order you follow (e.g. left to "
                      "right), give each element's position before describing it, approximate sizes by comparison "
                      "with everyday objects, and for an artwork its medium and material. If it is a screenshot of an app or web page, "
                      "say which window or page it is and list the main controls and messages. Be concise.")
        if question:
            prompt += f"\nAnswer this question first: \"{question}\""
        prompt += f"\nWrite in {description_language_name()}, plain text without Markdown."
        if backend == "openai":
            return _describe_with_openai_compatible(path, prompt)
        if local:
            return _describe_with_ollama(path, prompt)
        uploaded = _upload_and_wait_for_active(client, path)
        config = gemini.build_generation_config(is_json_response=False, enable_thinking=False)
        response = gemini.generate_content_with_retry(client, model=_get_model_name(), contents=[prompt, uploaded], config=config)
        gemini.log_token_usage("DescribeFile", response)
        text, ok = gemini.process_gemini_response(response, None)
        if not ok or not text:
            raise GeminiAPIError(_("AI could not provide an answer."))
        return text.strip()
    finally:
        _cleanup_uploaded_file(client, uploaded)


def story_context(descriptions, glossary, at_sec, window_sec=30):
    """Glossary + nearby script lines, so Ask More / the scene explorer use the same names (ViDscribe, SPICA)."""
    lines = []
    if glossary:
        lines.append("Character glossary (call people exactly by these names or descriptors):\n"
                     + json.dumps(glossary, ensure_ascii=False))
    near = [d for d in descriptions or () if abs(d[0] - at_sec) <= window_sec]
    if near:
        lines.append("Audio description script around this moment:\n"
                     + "\n".join(f"{timing.format_vtt_time(d[0])}: {d[2]}" for d in near))
    return "\n\n".join(lines)


def build_vqa_prompt(question, language_name, history=(), context=""):
    """Ask More prompt; earlier exchanges are included so follow-up questions
    ("and what is he holding?") keep their context."""
    lines = [context, ""] if context else []
    if history:
        lines.append("Earlier in this conversation about the same video:")
        for q, a in history:
            lines.append(f"Q: {q}\nA: {a}")
        lines.append("")
    lines.append(f"The clip ends at the moment the user paused on. Based on it, answer the user's question about "
                 f"that moment concisely in {language_name}: \"{question}\"")
    return "\n".join(lines)


def ask_gemini_about_video_segment(video_segment_path, vqa_prompt_text, system_instruction_text_vqa=None):
    _require_gemini_backend()
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
    _require_gemini_backend()
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

        if hasattr(response, 'candidates') and response.candidates and getattr(getattr(response.candidates[0], 'finish_reason', None), 'name', None) == 'MAX_TOKENS':
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
    return gemini.get_model_name()

def _mmss_to_total_seconds(mmss_string):
    """Accepts SS, MM:SS and HH:MM:SS (B4: hour-long videos were silently dropped)."""
    try:
        return timing.parse_timestamp(mmss_string)
    except (ValueError, TypeError) as e:
        app_logger.warning("Invalid timestamp '%s': %s" % (mmss_string, e))
        raise ValueError(_("Invalid timestamp format: %s") % mmss_string)

def _post_process_mmss_timestamps(raw_items, status_update_callback=None):
    """Parse times, enforce ordering and a minimum duration. Items are
    (start, end, text[, level[, on_screen_text]]); returns timing.Description list."""
    if not raw_items:
        return []
    corrected = []
    last_end = 0.0
    adjustments = 0
    for item in raw_items:
        start_raw, end_raw, text = item[0], item[1], item[2]
        level = item[3] if len(item) > 3 else timing.LEVEL_PRIMARY
        on_screen = bool(item[4]) if len(item) > 4 else False
        translation = bool(item[5]) if len(item) > 5 else False
        gender = item[6] if len(item) > 6 else None
        speaker = str(item[7]).strip() if len(item) > 7 and item[7] else None
        try:
            start = _mmss_to_total_seconds(start_raw)
            end = _mmss_to_total_seconds(end_raw)
        except ValueError:
            app_logger.warning("Skipping description with invalid timestamps: %r", item)
            continue
        adjusted = False
        if corrected and start < last_end:
            start = last_end + 0.001
            adjusted = True
        if end <= start:
            end = start + max(0.1, timing.estimate_speech_seconds(text))
            adjusted = True
        adjustments += adjusted
        flags = ((timing.FLAG_ON_SCREEN_TEXT,) if on_screen else ()) + ((timing.FLAG_TRANSLATION,) if translation else ())
        if translation and gender in timing.GENDER_FLAGS:
            flags += (timing.GENDER_FLAGS[gender],)
        if translation and speaker:
            flags += (timing.SPEAKER_PREFIX + speaker,)
        corrected.append(timing.Description(start, end, text, level, flags))
        last_end = end
    app_logger.info(f"Timestamp correction: {len(corrected)} kept, {adjustments} adjusted.")
    return corrected

def _remove_consecutive_duplicates(descriptions_list, status_update_callback=None):
    cleaned_list, duplicates_removed = timing.remove_consecutive_duplicates(descriptions_list or [])
    if duplicates_removed > 0 and status_update_callback:
        status_update_callback(_("Removed %(count)d repetitive descriptions.") % {'count': duplicates_removed})
    return cleaned_list


def _merge_glossary(glossary, new_entries):
    """Merge glossary entries by id instead of blindly extending (no duplicates across chunks)."""
    by_id = {g.get("id"): g for g in glossary if isinstance(g, dict)}
    for entry in new_entries:
        if not isinstance(entry, dict):
            continue
        existing = by_id.get(entry.get("id"))
        if existing is None:
            glossary.append(entry)
            by_id[entry.get("id")] = entry
        elif not existing.get("name") and entry.get("name"):
            existing["name"] = entry["name"]
        if existing is not None and existing is not entry and existing.get("apparent_gender") in (None, "unknown")                 and entry.get("apparent_gender") in ("female", "male"):
            existing["apparent_gender"] = entry["apparent_gender"]  # never touches "voice", the user's choice

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
                if start is not None and end is not None and text and str(text).strip():
                    descriptions.append((str(start), str(end), str(text).strip(),
                                         item.get("level", timing.LEVEL_PRIMARY),
                                         bool(item.get("on_screen_text", False)),
                                         bool(item.get("dialogue_translation", False)),
                                         item.get("speaker_gender"),
                                         item.get("speaker_id")))
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


# Prompt building lives in prompts.py; re-exported for existing callers (ollama_video, UI).
from .prompts import (  # noqa: E402,F401
    MAX_CUTS_IN_PROMPT, MAX_WINDOWS_IN_PROMPT, MIN_WINDOW_SEC, core_directives, load_rules, system_prompt,
    _build_unified_prompts, _format_cuts, _format_windows, _personalisation_lines, description_language_name,
)
