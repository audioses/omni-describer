# audio_describer/core/frames_video.py
"""Video descriptions with any OpenAI-compatible vision model (OpenRouter, OpenAI, LM Studio, Ollama...).

Those models take images, not video and not sound. The video is walked in ~25 s segments (one request
each): a few downscaled frames with their times, the pauses without speech found locally (Silero VAD,
when enabled) and the characters met so far. Replies come back as timed descriptions in the same
shape as the Gemini pipeline. A segment that still fails after the client's retries is skipped, so
the segments already described are kept."""
import base64
import glob
import json
import os
import tempfile

from .. import app_state
from ..i18n_setup import _
from ..models import config_model
from ..utils.logger import app_logger
from ..utils.system_utils import run_command
from . import openai_vision, prompts, timing, video_processor
from .gemini_helpers import GeminiAPIError

SEGMENT_SEC = 25
FRAME_STEP_SEC = 4
MAX_FRAMES = 6
FRAME_WIDTH = 512
WORDS_PER_SEC = 160 / 60      # ACB speaking rate, to estimate how long a description lasts
MAX_FAILED_SEGMENTS_IN_A_ROW = 2


def _segments(duration):
    bounds, start = [], 0.0
    while start < duration - 0.5:
        end = min(duration, start + SEGMENT_SEC)
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
               "-fps_mode", "vfr", "-frames:v", str(len(times)), "-q:v", "5", os.path.join(tmp, "f_%02d.jpg")]
        result = run_command(cmd)
        files = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        if result.returncode != 0 or not files:
            app_logger.warning(f"Frame extraction failed at {start:.1f}s: {(result.stderr or '')[-300:]}")
            return []
        frames = []
        for t, path in zip(times, files):
            with open(path, "rb") as f:
                frames.append((t, base64.b64encode(f.read()).decode("ascii")))
        return frames


def _speech_seconds(text):
    return max(1.0, len(text.split()) / WORDS_PER_SEC)


def parse_segment(raw, seg_start, seg_end, pauses):
    """Model JSON -> ([timing.Description], [glossary entries]); times clamped into the segment.
    A description ends with the pause it starts in, else after the time needed to say it."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        app_logger.warning(f"Vision model returned invalid JSON: {str(raw)[:300]}")
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
        speech_end = start + _speech_seconds(text)
        end = next((b for a, b in pauses or () if a - 0.3 <= start < b and b > speech_end), speech_end)
        descriptions.append(timing.Description(start, end, text))
    glossary = [g for g in data.get("character_glossary") or () if isinstance(g, dict) and g.get("id")]
    return descriptions, glossary


def _segment_prompt(base_prompt, frames, start, end, pauses, recent):
    lines = [base_prompt, "",
             f"This part of the video runs from {timing.format_mmss(start)} to {timing.format_mmss(end)} "
             f"({start:.1f} s to {end:.1f} s). The frames below were taken at: "
             + ", ".join(f"{t:.1f} s" for t, _b in frames) + "."]
    inside = [(max(a, start), min(b, end)) for a, b in pauses or () if b > start and a < end]
    if inside:
        lines.append("Pauses without speech in this part (seconds): "
                     + ", ".join(f"{a:.1f}-{b:.1f}" for a, b in inside)
                     + ". Place descriptions inside these pauses whenever possible.")
    elif pauses is not None:
        lines.append("There is speech all through this part: keep descriptions short and only for what matters.")
    if recent:
        lines.append("Already described just before (do not repeat): " + " / ".join(recent))
    return "\n".join(lines)


def describe_video(video_path, user_prompt="", status=None, initial_glossary=None, audio_source_path=None):
    """(descriptions, glossary) for the whole video, segment by segment."""
    status = status or (lambda _m: None)
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not video_processor.FFMPEG_IS_AVAILABLE:
        raise GeminiAPIError(_("FFmpeg is required to describe a video with another vision model."))
    model = openai_vision.settings()[2]  # fails early, before any work, when Settings are incomplete
    duration = video_processor.get_video_duration(video_path)
    if not duration or duration <= 0:
        raise GeminiAPIError(_("Could not read the length of the video."))
    status(_("Describing with %s (still frames, without sound).") % model)

    pauses = None
    if config_model.get_setting("detect_speech_pauses"):
        from . import vad
        status(_("Detecting pauses in the dialogue..."))
        pauses = vad.detect_speech_free_windows(audio_source_path or video_path, duration, min_gap=0.8)
        app_state.raise_if_cancelled()

    rules = prompts.load_rules()
    system = prompts._fill(rules.get("system_frames", "{core_directives}"), core_directives=prompts.core_directives(rules))
    glossary = [dict(g) for g in initial_glossary or ()]
    descriptions, failed_in_a_row, skipped = [], 0, []
    segments = _segments(duration)
    for index, (start, end) in enumerate(segments, 1):
        app_state.raise_if_cancelled()
        status(_("AI is thinking... part %(i)d of %(n)d") % {"i": index, "n": len(segments)})
        frames = _extract_frames(video_path, start, end)
        if not frames:
            continue
        base_prompt = prompts.build_unified_prompts(user_prompt, glossary or None)[1]
        recent = [d[2] for d in descriptions[-3:]]
        try:
            raw = openai_vision.chat(system, _segment_prompt(base_prompt, frames, start, end, pauses, recent),
                                     [b for _t, b in frames], json_output=True, status=status)
        except GeminiAPIError as e:
            if not descriptions:  # nothing to keep yet: show the real error (wrong key, model...)
                raise
            failed_in_a_row += 1
            skipped.append((start, end))
            app_logger.warning(f"Vision segment {start:.1f}-{end:.1f}s skipped: {e}")
            if failed_in_a_row >= MAX_FAILED_SEGMENTS_IN_A_ROW:
                app_logger.warning("Vision service keeps failing; keeping the descriptions made so far.")
                skipped.append((end, duration))
                break
            continue
        failed_in_a_row = 0
        new, new_glossary = parse_segment(raw, start, end, pauses)
        descriptions.extend(new)
        glossary = timing.merge_glossary(glossary, new_glossary)
    if skipped:
        spans = ", ".join(f"{timing.format_mmss(a)}-{timing.format_mmss(b)}" for a, b in skipped)
        status(_("Some parts could not be described (%s); the rest is kept.") % spans)
    descriptions.sort(key=lambda d: d[0])
    app_logger.info(f"Frames backend: {len(descriptions)} descriptions, {len(glossary)} characters, skipped {skipped}")
    return descriptions, glossary
