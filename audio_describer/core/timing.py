# audio_describer/core/timing.py
"""Pure timing helpers. Stdlib only (no wx, no SDK) so they can be unit-tested in CI."""

import math
import re

# ACB Audio Description Standards: 160 words/min ≈ 2.7 words/s.
WORDS_PER_SECOND = 160 / 60.0

# Single source for language codes -> names sent to the model (was duplicated).
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "ar": "Arabic", "pt": "Portuguese",
                  "it": "Italian", "ru": "Russian", "uk": "Ukrainian", "vi": "Vietnamese", "tr": "Turkish",
                  "de": "German", "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "nl": "Dutch", "pl": "Polish"}

# CAB/AMI levels: 1 primary (essential), 2 secondary (important), 3 tertiary (style/detail).
LEVEL_PRIMARY, LEVEL_SECONDARY, LEVEL_TERTIARY = 1, 2, 3

FLAG_OVER_BUDGET = "over_budget"          # more words than the window allows at 160 wpm
FLAG_OVERLAPS_SOUND = "overlaps_sound"    # placed outside every detected quiet window
FLAG_ON_SCREEN_TEXT = "on_screen_text"    # reads on-screen text (earcon before it)
FLAG_TRANSLATION = "translation"          # audio subtitle: translated foreign dialogue (may overlap speech)
FLAG_UNCERTAIN = "uncertain"              # AI reviewer suspects a hallucination or inconsistency
# Style issues found by the AI reviewer (audit §3.5, Hélène; CSA 2020 grid)
FLAG_PAST_TENSE = "past_tense"            # AD is written in the present tense
FLAG_INTERPRETATION = "interpretation"    # "he seems sad" instead of "he lowers his eyes"
FLAG_OUT_OF_FICTION = "out_of_fiction"    # actors' names, "the camera pans", filming jargon
STYLE_FLAGS = (FLAG_PAST_TENSE, FLAG_INTERPRETATION, FLAG_OUT_OF_FICTION)
FLAG_VALIDATED = "validated"              # reviewer status (audit §3.5); absent = to review
# Speaker's gender for translated dialogue: read with a matching voice when one is available.
FLAG_VOICE_FEMALE = "voice_female"
FLAG_VOICE_MALE = "voice_male"
GENDER_FLAGS = {"female": FLAG_VOICE_FEMALE, "male": FLAG_VOICE_MALE}
# Voice casting: "speaker:<glossary id>" ties a translated line to a character; "cast:<voice>"
# mirrors that character's voice override (glossary "voice") and wins over the line's gender.
SPEAKER_PREFIX = "speaker:"
CAST_PREFIX = "cast:"
CAST_VOICES = ("female", "male", "narrator")


class Description(tuple):
    """(start, end, text) that also carries a density level and quality flags.

    Still a 3-tuple, so every `for start, end, text in descriptions` keeps working;
    plain tuples (e.g. edited in the editor) are treated as level 1, no flags.
    """
    def __new__(cls, start, end, text, level=LEVEL_PRIMARY, flags=(), note=""):
        obj = super().__new__(cls, (float(start), float(end), str(text)))
        obj.level = int(level) if str(level) in ("1", "2", "3") else LEVEL_PRIMARY
        obj.flags = tuple(flags)
        obj.note = str(note or "")  # reviewer's remark (audit §3.5)
        return obj

    def __reduce__(self):
        return (Description, (self[0], self[1], self[2], self.level, self.flags, self.note))


def level_of(desc):
    return getattr(desc, "level", LEVEL_PRIMARY)


def flags_of(desc):
    return getattr(desc, "flags", ())


def note_of(desc):
    return getattr(desc, "note", "")


def word_budget(window_seconds, words_per_second=WORDS_PER_SECOND):
    return max(1, math.floor(window_seconds * words_per_second))


def parse_silencedetect(ffmpeg_stderr, media_duration=None):
    """Parse `ffmpeg -af silencedetect` output into [(start, end), ...] quiet windows."""
    windows, current = [], None
    for line in ffmpeg_stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            current = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and current is not None:
            windows.append((current, float(m.group(1))))
            current = None
    if current is not None and media_duration:
        windows.append((current, float(media_duration)))
    return windows


def speech_free_windows(probs, frame_sec, duration=None, threshold=0.5, min_gap=0.8, pad=0.1):
    """Silero frame probabilities -> [(start, end), ...] without speech, at least min_gap long.
    Speech is padded by `pad` seconds so a description never clips the first or last syllable."""
    windows, gap_start = [], 0.0
    for i, p in enumerate(probs):
        if p >= threshold:
            if gap_start is not None:
                end = i * frame_sec - pad
                if end - gap_start >= min_gap:
                    windows.append((round(gap_start, 3), round(end, 3)))
                gap_start = None
        elif gap_start is None:
            gap_start = i * frame_sec + pad
    total = duration or len(probs) * frame_sec
    if gap_start is not None and total - gap_start >= min_gap:
        windows.append((round(gap_start, 3), round(total, 3)))
    return windows


def parse_scene_changes(ffmpeg_stderr):
    """Times of shot changes from `select='gt(scene,X)',showinfo` output."""
    return [float(m) for m in re.findall(r"Parsed_showinfo.*?pts_time:\s*([\d.]+)", ffmpeg_stderr)]


def windows_in_range(windows, start, end):
    """Quiet windows clipped to [start, end), shifted to be relative to start."""
    out = []
    for a, b in windows:
        a2, b2 = max(a, start), min(b, end)
        if b2 - a2 > 0.05:
            out.append((a2 - start, b2 - start))
    return out


def compute_flags(start, end, text, quiet_windows=None, on_screen_text=False, translation=False, gender=None):
    """Quality flags for the editor/player. quiet_windows=None means 'not analysed'.
    Translations of foreign dialogue are spoken over that dialogue by design: no overlap flag.
    gender: 'female' / 'male' speaker of a translated line (anything else: unknown)."""
    flags = []
    if len(str(text).split()) > word_budget(end - start) + 1:
        flags.append(FLAG_OVER_BUDGET)
    if translation:
        flags.append(FLAG_TRANSLATION)
        if gender in GENDER_FLAGS:
            flags.append(GENDER_FLAGS[gender])
    elif quiet_windows:
        speech_end = start + estimate_speech_seconds(text)
        inside = any(a - 0.3 <= start and speech_end <= b + 0.5 for a, b in quiet_windows)
        if not inside:
            flags.append(FLAG_OVERLAPS_SOUND)
    if on_screen_text:
        flags.append(FLAG_ON_SCREEN_TEXT)
    return tuple(flags)


def line_gender(desc):
    """The speaker's apparent gender stored on a translated line ('female' / 'male'), else None."""
    flags = flags_of(desc)
    return next((g for g, f in GENDER_FLAGS.items() if f in flags), None)


def _prefixed(desc, prefix):
    return next((f[len(prefix):] for f in flags_of(desc) if f.startswith(prefix)), None)


def speaker_of(desc):
    """Glossary id of the character speaking a translated line, if the model gave one."""
    return _prefixed(desc, SPEAKER_PREFIX)


def voice_gender(desc):
    """Voice reading a translated line: the character's cast voice (glossary), else the line's
    apparent speaker gender; None = the description (narrator) voice."""
    cast = _prefixed(desc, CAST_PREFIX)
    if cast in CAST_VOICES:
        return None if cast == "narrator" else cast
    return line_gender(desc)


def speaker_flags(desc):
    """Speaker and cast flags, to carry over when a line's flags are recomputed."""
    return tuple(f for f in flags_of(desc) if f.startswith((SPEAKER_PREFIX, CAST_PREFIX)))


def apply_voice_casting(descriptions, glossary):
    """Mirror each character's 'voice' override (glossary) onto its lines as a cast flag, so every
    reader of voice_gender() honours it. Returns (new_list, changed_count)."""
    casts = {g.get("id"): g.get("voice") for g in glossary or ()
             if isinstance(g, dict) and g.get("voice") in CAST_VOICES}
    out, changed = [], 0
    for d in descriptions:
        old = tuple(flags_of(d))
        flags = tuple(f for f in old if not f.startswith(CAST_PREFIX))
        voice = casts.get(speaker_of(d))
        if voice:
            flags += (CAST_PREFIX + voice,)
        if flags != old:
            d = Description(d[0], d[1], d[2], level_of(d), flags, note_of(d))
            changed += 1
        out.append(d)
    return out, changed


def is_read_text(desc):
    """On-screen text or translated dialogue: announced by an earcon, read in the second voice."""
    return bool({FLAG_ON_SCREEN_TEXT, FLAG_TRANSLATION} & set(flags_of(desc)))


def dhash(gray, width=9, height=8):
    """Difference hash of a width x height greyscale image (row-major 0-255 values) -> 64-bit int."""
    bits = 0
    for y in range(height):
        row = gray[y * width:(y + 1) * width]
        for x in range(width - 1):
            bits = (bits << 1) | (row[x] > row[x + 1])
    return bits


def hash_distance(a, b):
    return bin(a ^ b).count("1")


def needs_extended_pause(desc):
    """True when the description cannot be spoken inside its own window (WCAG 1.2.7 extended AD)."""
    return estimate_speech_seconds(desc[2]) > (desc[1] - desc[0]) + 0.2


def format_vtt_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def overlay_text(text, width=42):
    """Large on-video text (audit §6.2): wrapped lines, VLC marquee escapes for % and $."""
    import textwrap
    return textwrap.fill(" ".join(text.split()), width).replace("%", "%%").replace("$", "$$")


# Gemini video tokens (audit §6.3, Gemini API docs): per frame and per second of audio.
VIDEO_TOKEN_RATES = {"gemini-2": (258, 32), "gemini-3": (70, 25)}


def estimate_video_tokens(duration_s, fps, model_name, with_audio=True):
    """Input tokens to send the video once; the model's answer adds a few thousand."""
    per_frame, per_audio_s = VIDEO_TOKEN_RATES["gemini-2" if "gemini-2" in (model_name or "") else "gemini-3"]
    return int(duration_s * ((fps or 1) * per_frame + (per_audio_s if with_audio else 0)))


def format_srt_time(seconds):
    ms = int(round(max(0.0, float(seconds)) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(descriptions):
    return "".join(f"{i + 1}\n{format_srt_time(d[0])} --> {format_srt_time(d[1])}\n{d[2]}\n\n"
                   for i, d in enumerate(descriptions))


def to_text_lines(descriptions):
    return "".join(f"{d[2]}\n" for d in descriptions)


def to_webvtt(descriptions):
    """WebVTT with kind="descriptions" semantics: one cue per description."""
    lines = ["WEBVTT", "", "NOTE Text audio descriptions. In HTML, load this file with",
             '<track kind="descriptions" srclang="(language)" label="Audio description" src="(file)">', ""]
    for i, (start, end, text) in enumerate(descriptions, 1):
        lines += [str(i), f"{format_vtt_time(start)} --> {format_vtt_time(max(end, start + 0.5))}",
                  text.replace("-->", "->"), ""]
    return "\n".join(lines)


def parse_timestamp(ts):
    """Parse 'SS', 'MM:SS' or 'HH:MM:SS' (optional .ms or ,ms) into float seconds.

    Raises ValueError on malformed or negative input.
    """
    if isinstance(ts, (int, float)):
        parts = [str(ts)]
    else:
        parts = str(ts).strip().replace(',', '.').split(':')
    if not 1 <= len(parts) <= 3 or any(not p.strip() for p in parts):
        raise ValueError(f"Invalid timestamp: {ts!r}")
    total = 0.0
    for p in parts:
        value = float(p)
        if value < 0:
            raise ValueError(f"Negative timestamp component: {ts!r}")
        total = total * 60 + value
    return total


def format_hms(seconds):
    """Format seconds as H:MM:SS (or MM:SS under an hour) for display/announcements."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def estimate_speech_seconds(text, words_per_second=WORDS_PER_SECOND):
    """Rough spoken duration of a description at the AD reference rate."""
    return len(str(text).split()) / words_per_second


def db_to_volume(base_volume, attenuation_db):
    """Lower a 0-100 VLC volume by attenuation_db decibels (positive number = quieter)."""
    return int(round(base_volume * 10 ** (-abs(attenuation_db) / 20.0)))


def remove_consecutive_duplicates(descriptions):
    """Drop entries whose text repeats the previous kept entry. Returns (cleaned, removed_count)."""
    cleaned = []
    for item in descriptions:
        if cleaned and item[2].strip() == cleaned[-1][2].strip():
            continue
        cleaned.append(item)
    return cleaned, len(descriptions) - len(cleaned)


def merge_intervals(intervals, gap=0.0):
    """Merge [(a, b)] intervals that overlap or are closer than `gap` seconds."""
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [tuple(m) for m in merged]


def ducking_volume_expr(intervals, attenuation_db, ramp_down=0.15, ramp_up=0.4):
    """FFmpeg `volume` expression: programme lowered by attenuation_db during each
    interval, reached through a ramp_down before it and released over ramp_up after
    (Netflix AD guide: dip, never mute, no abrupt transitions)."""
    gain = 10 ** (-abs(attenuation_db) / 20.0)
    merged = merge_intervals(intervals, gap=ramp_down + ramp_up)
    if not merged:
        return "1"
    terms = [f"clip((t-{a - ramp_down:.3f})/{ramp_down},0,1)*clip(({b + ramp_up:.3f}-t)/{ramp_up},0,1)"
             for a, b in merged]
    return f"1-{1 - gain:.4f}*min(1,{'+'.join(terms)})"


def on_demand_texts(descriptions, now, detailed, lookback=15.0):
    """Brief: the latest essential description at or before now. Detailed: every
    description of the last `lookback` seconds, all levels."""
    recent = [d for d in descriptions if now - lookback <= d[0] <= now + 0.5]
    if detailed:
        return [d[2] for d in recent]
    essential = [d for d in recent if level_of(d) == LEVEL_PRIMARY] or recent
    return [essential[-1][2]] if essential else []


def rename_in_descriptions(descriptions, old, new):
    """Whole-word, case-sensitive rename in description texts. Returns (new_list, changed_count)."""
    pattern = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)")
    out, changed = [], 0
    for d in descriptions:
        text = pattern.sub(new, d[2])
        if text != d[2]:
            changed += 1
            d = Description(d[0], d[1], text, level_of(d), flags_of(d), note_of(d))
        out.append(d)
    return out, changed


def quality_report(descriptions, duration=None):
    """Audit §9 indicators computed from the script alone."""
    n = len(descriptions)
    count = lambda flag: sum(1 for d in descriptions if flag in flags_of(d))
    durations = [max(0.1, d[1] - d[0]) for d in descriptions]
    words = sum(len(d[2].split()) for d in descriptions)
    starts = sorted(d[0] for d in descriptions)
    edges = [0.0] + starts + ([duration] if duration else [])
    longest_gap = max((b - a for a, b in zip(edges, edges[1:])), default=0.0)
    return {
        "count": n,
        "over_budget": count(FLAG_OVER_BUDGET),
        "overlaps_sound": count(FLAG_OVERLAPS_SOUND),
        "uncertain": count(FLAG_UNCERTAIN),
        "style": sum(1 for d in descriptions if set(STYLE_FLAGS) & set(flags_of(d))),
        "words_per_second": (words / sum(durations)) if n else 0.0,
        "within_budget_pct": (100.0 * (n - count(FLAG_OVER_BUDGET)) / n) if n else 100.0,
        "longest_gap": longest_gap,
    }


def filter_by_level(descriptions, max_level):
    """Density at playback (low=1, medium=2, high=3) without regenerating."""
    return [d for d in descriptions if level_of(d) <= max_level]


def crossed_description_index(descriptions, prev_sec, now_sec):
    """Index of the description that should be active at now_sec.

    Also catches a description that started and ended entirely between two
    timer ticks (prev_sec, now_sec] so short entries are not skipped (B12).
    Returns -1 if none.
    """
    for i, (start, end, _text) in enumerate(descriptions):
        if start <= now_sec < end:
            return i
    if 0 <= now_sec - prev_sec <= 1.0:
        for i, (start, _end, _text) in enumerate(descriptions):
            if prev_sec < start <= now_sec:
                return i
    return -1


WARNING_FLAGS = (FLAG_OVER_BUDGET, FLAG_OVERLAPS_SOUND, FLAG_UNCERTAIN) + STYLE_FLAGS


def next_to_review(descriptions, current, step=1):
    """Index of the next (step=1) or previous (step=-1) description with a warning that is not
    validated yet, wrapping around; None if there is none (editor F8 / Shift+F8)."""
    n = len(descriptions)
    for k in range(1, n + 1):
        i = (current + step * k) % n
        flags = flags_of(descriptions[i])
        if FLAG_VALIDATED not in flags and set(flags) & set(WARNING_FLAGS):
            return i
    return None


def extended_freezes(descriptions, clip_seconds, min_freeze=0.05):
    """Extended AD for exports (WCAG 1.2.7, EN 301 549 §7.2.3): [(source_time, freeze_seconds)].

    A clip longer than the time available (its own pause, never past the next description)
    freezes the programme at the end of that time for the overflow, instead of being cut."""
    freezes = []
    for i, (desc, clip) in enumerate(zip(descriptions, clip_seconds)):
        start, end = desc[0], desc[1]
        next_start = descriptions[i + 1][0] if i + 1 < len(descriptions) else float("inf")
        available = min(end if end > start else next_start, next_start) - start
        overflow = clip - max(0.0, available)
        if overflow > min_freeze:
            freezes.append((round(start + max(0.0, available), 3), round(overflow, 3)))
    return freezes


def shift_for_freezes(t, freezes):
    """Source time -> output time once the freezes are inserted."""
    return t + sum(f for p, f in freezes if p <= t)


def freeze_offset_expr(freezes):
    """FFmpeg expression of the cumulative freeze at input time T (setpts / asetpts)."""
    return "+".join(f"{f:.3f}*gte(T,{p:.3f})" for p, f in freezes) or "0"


_CUE_TIME = re.compile(r"(\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}")


def parse_subtitles(text):
    """SRT or WebVTT -> [Description] (audit §7.1: import an existing script to voice or edit it).
    Cue settings, numbers, NOTE/STYLE blocks and simple tags (<i>, <v Name>) are ignored."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        timing_index = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing_index is None:
            continue
        left, _arrow, right = lines[timing_index].partition("-->")
        start_m, end_m = _CUE_TIME.search(left), _CUE_TIME.search(right)
        if not (start_m and end_m):
            continue
        body = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in lines[timing_index + 1:]).strip()
        if not body:
            continue
        start, end = parse_timestamp(start_m.group(0)), parse_timestamp(end_m.group(0))
        out.append(Description(start, max(end, start), body, flags=compute_flags(start, max(end, start), body)))
    return sorted(out, key=lambda d: d[0])


def apply_pronunciations(text, spec):
    """User pronunciation dictionary for the TTS voice: "word = how to say it", one rule per line.
    Whole words, case-insensitive; longer words first so "New York" wins over "York"."""
    rules = []
    for line in (spec or "").splitlines():
        word, sep, say = line.partition("=")
        if sep and word.strip() and say.strip():
            rules.append((word.strip(), say.strip()))
    for word, say in sorted(rules, key=lambda r: -len(r[0])):
        text = re.sub(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lambda _m: say, text, flags=re.IGNORECASE)
    return text
