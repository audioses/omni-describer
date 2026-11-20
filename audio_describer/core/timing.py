# audio_describer/core/timing.py
"""Pure description helpers. Stdlib only (no wx, no SDK) so they can be unit-tested in CI."""

FLAG_TRANSLATION = "translation"          # audio subtitle: translated foreign dialogue
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
    """(start, end, text) that also carries flags.

    Still a 3-tuple, so every `for start, end, text in descriptions` keeps working;
    plain tuples (e.g. edited in the editor) simply have no flags.
    """
    def __new__(cls, start, end, text, flags=()):
        obj = super().__new__(cls, (float(start), float(end), str(text)))
        obj.flags = tuple(flags)
        return obj

    def __reduce__(self):
        return (Description, (self[0], self[1], self[2], self.flags))


def flags_of(desc):
    return getattr(desc, "flags", ())


def dialogue_flags(translation, gender=None, speaker=None):
    """Flags for a line from the AI: translated dialogue carries its speaker's gender and glossary id."""
    if not translation:
        return ()
    flags = (FLAG_TRANSLATION,)
    if gender in GENDER_FLAGS:
        flags += (GENDER_FLAGS[gender],)
    if speaker:
        flags += (SPEAKER_PREFIX + str(speaker).strip(),)
    return flags


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
            d = Description(d[0], d[1], d[2], flags)
            changed += 1
        out.append(d)
    return out, changed


def merge_glossary(glossary, new_entries):
    """Merge glossary entries by id instead of blindly extending (no duplicates across chunks or
    with characters reused from an earlier video). Never touches "voice", the user's choice."""
    by_id = {g.get("id"): g for g in glossary if isinstance(g, dict)}
    for entry in new_entries or ():
        if not isinstance(entry, dict):
            continue
        existing = by_id.get(entry.get("id"))
        if existing is None:
            glossary.append(entry)
            by_id[entry.get("id")] = entry
        elif existing.get("apparent_gender") in (None, "unknown") and entry.get("apparent_gender") in ("female", "male"):
            existing["apparent_gender"] = entry["apparent_gender"]
    return glossary
