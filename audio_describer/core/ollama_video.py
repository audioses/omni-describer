# audio_describer/core/ollama_video.py
"""Video descriptions with a local Ollama vision model: free, offline, nothing leaves the computer.

The video is walked in ~25 s segments (cut on a shot change when one is near); each segment
sends a few downscaled frames with their timestamps, the quiet windows and the story so far
in one request, and gets back timed descriptions in the same shape as the Gemini pipeline."""
import base64
import glob
import json
import os
import tempfile
import urllib.error
import urllib.request

from .. import app_state
from ..i18n_setup import _
from ..models import config_model
from ..utils.logger import app_logger
from ..utils.system_utils import run_command
from . import audio_describer as ad
from . import prompts, timing, video_processor
from .gemini_helpers import GeminiAPIError

SEGMENT_SEC = 25
FRAME_STEP_SEC = 4
MAX_FRAMES = 6
FRAME_WIDTH = 512
REQUEST_TIMEOUT_SEC = 600

SCHEMA = {
    "type": "object",
    "properties": {
        "audio_descriptions": {"type": "array", "items": {"type": "object", "properties": {
            "start_sec": {"type": "number"},
            "description_text": {"type": "string"},
            "level": {"type": "integer", "enum": [1, 2, 3]},
            "on_screen_text": {"type": "boolean"}},
            "required": ["start_sec", "description_text", "level", "on_screen_text"]}},
        "character_glossary": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "description": {"type": "string"}, "name": {"type": ["string", "null"]}},
            "required": ["id", "description"]}},
    },
    "required": ["audio_descriptions"],
}

# System prompt text: prompts/rules/en.json ("system_frames"), built at call time so edits apply at once.


def _read(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _segments(duration, cuts):
    """[(start, end), ...] of about SEGMENT_SEC, ending on a shot change when one is close."""
    bounds, start = [], 0.0
    while start < duration - 0.5:
        end = min(duration, start + SEGMENT_SEC)
        near = [c for c in cuts or () if start + SEGMENT_SEC * 0.6 <= c <= start + SEGMENT_SEC * 1.4 and c < duration]
        if near and end < duration:
            end = min(near, key=lambda c: abs(c - start - SEGMENT_SEC))
        bounds.append((start, end))
        start = end
    return bounds


def _frame_times(start, end):
    step = max(FRAME_STEP_SEC, (end - start) / MAX_FRAMES)
    times, t = [], start
    while t < end and len(times) < MAX_FRAMES:
        times.append(t)
        t += step
    return times, step


def _extract_frames(video_path, start, end):
    """[(time, base64 jpeg), ...] for one segment, in a single ffmpeg call."""
    times, step = _frame_times(start, end)
    with tempfile.TemporaryDirectory(prefix="omni_frames_") as tmp:
        cmd = [video_processor.FFMPEG_COMMAND, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
               "-i", video_path, "-t", f"{end - start:.3f}", "-an", "-sn", "-dn",
               "-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step - 0.02:.3f})',scale={FRAME_WIDTH}:-2",
               "-fps_mode", "vfr", "-frames:v", str(len(times)),
               "-q:v", "5", os.path.join(tmp, "f_%02d.jpg")]
        result = run_command(cmd)
        files = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        if result.returncode != 0 or not files:
            app_logger.warning(f"Frame extraction failed at {start:.1f}s: {result.stderr[-300:]}")
            return []
        frames = []
        for t, path in zip(times, files):
            with open(path, "rb") as f:
                frames.append((t, base64.b64encode(f.read()).decode("ascii")))
        return frames


def _ask_ollama(model, prompt, images):
    body = json.dumps({"model": model, "system": prompts.system_prompt("frames"), "prompt": prompt, "images": images,
                       "format": SCHEMA, "stream": False, "options": {"temperature": 0.2}}).encode("utf-8")
    request = urllib.request.Request(ad.OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        answer = json.loads(app_state.run_cancellable(_read, request, REQUEST_TIMEOUT_SEC).decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 404:
            raise GeminiAPIError(_("The model '%(model)s' is not installed in Ollama. Run: ollama pull %(model)s")
                                 % {"model": model}) from e
        raise GeminiAPIError(_("Ollama refused the request (model '%(model)s'): %(detail)s") % {"model": model, "detail": detail}) from e
    except TimeoutError as e:
        raise GeminiAPIError(_("Ollama took too long to answer. Try a smaller model or a shorter video.")) from e
    except (urllib.error.URLError, OSError) as e:
        raise GeminiAPIError(_("Ollama is not running on this computer. Install it from ollama.com, then run: ollama pull %s") % model) from e
    return answer.get("response") or ""


def _parse(raw, seg_start, seg_end, quiet_windows):
    """Model JSON -> ([timing.Description], [glossary entries]); times clamped into the segment."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        app_logger.warning(f"Ollama returned invalid JSON: {str(raw)[:300]}")
        return [], []
    if not isinstance(data, dict):
        return [], []
    descriptions = []
    for item in data.get("audio_descriptions") or ():
        if not isinstance(item, dict):
            continue
        text = str(item.get("description_text") or "").strip()
        try:
            start = float(item.get("start_sec"))
        except (TypeError, ValueError):
            continue
        if not text:
            continue
        start = min(max(start, seg_start), max(seg_start, seg_end - 0.5))
        speech_end = start + timing.estimate_speech_seconds(text)
        # Like the Gemini path, a description ends with the pause it sits in.
        end = next((b for a, b in quiet_windows or () if a - 0.3 <= start < b and b > speech_end), speech_end)
        on_screen_text = bool(item.get("on_screen_text"))
        descriptions.append(timing.Description(start, end, text, item.get("level", timing.LEVEL_PRIMARY),
                                               timing.compute_flags(start, end, text, quiet_windows,
                                                                    on_screen_text=on_screen_text)))
    glossary = [g for g in data.get("character_glossary") or () if isinstance(g, dict) and g.get("id")]
    return descriptions, glossary


def describe_video_locally(video_path, user_prompt, status, cancel_event, initial_glossary=None):
    """Same contract as generate_descriptions_chunked: (descriptions, glossary, token_usage=[])."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not video_processor.FFMPEG_IS_AVAILABLE:
        raise GeminiAPIError(_("FFmpeg is required to describe a video with the local model."))
    duration = video_processor.get_video_duration(video_path)
    if duration <= 0:
        raise GeminiAPIError(_("Could not read the length of the video."))
    remote = config_model.get_setting("video_backend") == "openai"
    if remote:
        from . import openai_vision
        model = openai_vision.settings()[2]
        status(_("Describing with %s (frames sent to the vision service).") % model)
    else:
        model = config_model.get_setting("ollama_model") or "gemma3"
        status(_("Describing locally with Ollama (%s). Nothing leaves this computer.") % model)

    quiet_windows = cuts = None
    if config_model.get_setting("enable_silence_detection"):
        status(_("Detecting pauses in the soundtrack..."))
        quiet_windows = video_processor.detect_quiet_windows(video_path, duration)
        if quiet_windows is not None:
            status(_("Found %d pauses in the soundtrack.") % len(quiet_windows))
        ad._check_cancel(cancel_event)
        status(_("Detecting shot changes..."))
        cuts = video_processor.detect_shot_changes(video_path)
        ad._check_cancel(cancel_event)

    glossary = [dict(g) for g in (initial_glossary or [])]
    descriptions = []
    segments = _segments(duration, cuts)
    for n, (start, end) in enumerate(segments, 1):
        ad._check_cancel(cancel_event)
        status(_("Segment %(n)d of %(total)d...") % {"n": n, "total": len(segments)})
        frames = _extract_frames(video_path, start, end)
        if not frames:
            continue
        seg_windows = None if quiet_windows is None else [(a, b) for a, b in quiet_windows if b > start and a < end]
        seg_cuts = [c for c in cuts or () if start <= c < end]
        context = ad._chunk_context(glossary, descriptions, last_n=3)
        _system, prompt = ad._build_unified_prompts(user_prompt, model, quiet_windows=seg_windows,
                                                    context=context, shot_changes=seg_cuts)
        prompt += (f"\n\n**This part of the video:** from {start:.1f} s to {end:.1f} s. The images are, in order:\n"
                   + "\n".join(f"- Image {i}: {t:.1f} s ({timing.format_vtt_time(t)})" for i, (t, _b) in enumerate(frames, 1))
                   + f"\nGive every \"start_sec\" between {start:.1f} and {end:.1f}. Reply with the JSON object only.")
        images = [b for _t, b in frames]
        raw = (openai_vision.chat(prompts.system_prompt("frames"), prompt, images, json_output=True) if remote
               else _ask_ollama(model, prompt, images))
        new, new_glossary = _parse(raw, start, end, quiet_windows)
        ad._merge_glossary(glossary, new_glossary)
        descriptions += new
        app_logger.info(f"Ollama segment {n}/{len(segments)} ({start:.1f}-{end:.1f}s): {len(new)} descriptions")

    descriptions.sort(key=lambda d: d[0])
    descriptions = ad._remove_consecutive_duplicates(descriptions, status)
    flagged = sum(1 for d in descriptions if {timing.FLAG_OVER_BUDGET, timing.FLAG_OVERLAPS_SOUND} & set(d.flags))
    status(_("%(count)d descriptions ready, %(flagged)d to review.") % {"count": len(descriptions), "flagged": flagged})
    return descriptions, glossary, []
