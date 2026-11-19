# audio_describer/models/evaluation_model.py
"""'Evaluate this AD' (audit §10.6 bis): the CSA 2020 guide's two-phase questionnaire,
rated 1-5 per question with written comments. No average: the guide says it is meaningless."""
from ..i18n_setup import _
from ..core import timing

GLOBAL, ANALYTIC = "global", "analytic"


def questions():
    """(key, phase, question). Built at call time so the current UI language applies."""
    return [
        ("understood", GLOBAL, _("Without the picture, are the action, the characters, the place and the time understood?")),
        ("no_dialogue_overlap", GLOBAL, _("Does the description avoid covering dialogue?")),
        ("describes_images", GLOBAL, _("Does it describe the images rather than explain the plot?")),
        ("right_amount", GLOBAL, _("Is it neither too sparse nor too present?")),
        ("in_fiction", GLOBAL, _("Does it stay inside the fiction (no actors, no filming jargon)?")),
        ("language", GLOBAL, _("Is the language correct and clear?")),
        ("voice_pace", GLOBAL, _("Are the voice and the pace pleasant and suitable?")),
        ("no_errors", ANALYTIC, _("With the picture: is it free of factual errors and omissions?")),
        ("interpretation", ANALYTIC, _("Are looks, faces and gestures interpreted correctly?")),
        ("chronology", ANALYTIC, _("Are the chronology and the rhythm of the film respected?")),
        ("sound_placement", ANALYTIC, _("Is each description well placed relative to sound effects and music?")),
        ("on_screen_text", ANALYTIC, _("Is on-screen text read?")),
        ("style", ANALYTIC, _("Does the style match the film?")),
    ]


def auto_hints(descriptions, duration=None):
    """Measurements shown next to the questions they inform."""
    r = timing.quality_report(descriptions, duration)
    n = r["count"]
    read_text = sum(1 for d in descriptions if timing.FLAG_ON_SCREEN_TEXT in timing.flags_of(d))
    flagged = lambda flag: sum(1 for d in descriptions if flag in timing.flags_of(d))
    return {
        "no_dialogue_overlap": _("measured: %(n)d of %(total)d descriptions over sound or dialogue") % {"n": r["overlaps_sound"], "total": n},
        "right_amount": _("measured: %(total)d descriptions, longest stretch without description %(gap)s") % {"total": n, "gap": timing.format_hms(r["longest_gap"])},
        "voice_pace": _("measured: %(wps).1f words per second (target 2.7 or less)") % {"wps": r["words_per_second"]},
        "no_errors": _("measured: %d descriptions marked uncertain by the AI check") % r["uncertain"],
        "describes_images": _("measured: %d descriptions interpret instead of describing") % flagged(timing.FLAG_INTERPRETATION),
        "in_fiction": _("measured: %d descriptions leave the fiction") % flagged(timing.FLAG_OUT_OF_FICTION),
        "language": _("measured: %d descriptions not in the present tense") % flagged(timing.FLAG_PAST_TENSE),
        "on_screen_text": _("measured: %d descriptions read on-screen text") % read_text,
    }


def build_report(title, ratings, comments, hints):
    """Plain-text report: one line per question, then the comments of each phase."""
    labels = {None: _("not rated"), 1: _("1, very poor"), 2: _("2, poor"), 3: _("3, fair"), 4: _("4, good"), 5: _("5, very good")}
    phase_titles = {GLOBAL: _("Global listening, without the picture"), ANALYTIC: _("Analysis, with the picture")}
    lines = [_("Audio description evaluation (CSA 2020 grid): %s") % title, ""]
    for phase in (GLOBAL, ANALYTIC):
        lines.append(phase_titles[phase])
        for key, p, question in questions():
            if p != phase:
                continue
            line = f"- {question} {labels.get(ratings.get(key))}"
            if key in hints:
                line += f" ({hints[key]})"
            lines.append(line)
        if comments.get(phase, "").strip():
            lines += [_("Comments:"), comments[phase].strip()]
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    descs = [timing.Description(1, 3, "Sign: EXIT.", 1, (timing.FLAG_ON_SCREEN_TEXT, timing.FLAG_OVERLAPS_SOUND))]
    hints = auto_hints(descs, 10)
    report = build_report("film", {"understood": 4, "on_screen_text": 5}, {GLOBAL: " Clear. "}, hints)
    assert "- Without the picture, are the action, the characters, the place and the time understood? 4, good" in report, report
    assert "1 of 1 descriptions over sound" in report and "1 descriptions read on-screen text" in report
    assert "Comments:\nClear." in report and "not rated" in report
    assert len(questions()) == 13 and len({k for k, _p, _q in questions()}) == 13
    print("evaluation_model self-check OK")
