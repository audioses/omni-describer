# audio_describer/models/project_model.py
"""Save/load Omni Describer projects (.omniad, plain JSON) so generated work is never lost."""
import json
import os

from ..core.timing import Description, level_of, flags_of, note_of

PROJECT_EXTENSION = ".omniad"
PROJECT_FORMAT_VERSION = 1


def save_project(project_path, video_path, descriptions, character_glossary=None, token_usage=None, audio_introduction="",
                 evaluation=None):
    """Write the project atomically. The video path is also stored relative to the
    project so a folder containing both can be moved."""
    project_dir = os.path.dirname(os.path.abspath(project_path))
    try:
        rel_video = os.path.relpath(video_path, project_dir)
    except ValueError:  # different drive on Windows
        rel_video = None
    data = {
        "format": "omniad",
        "version": PROJECT_FORMAT_VERSION,
        "video_path": os.path.abspath(video_path),
        "video_path_relative": rel_video,
        "descriptions": [{"start": float(d[0]), "end": float(d[1]), "text": str(d[2]),
                          "level": level_of(d), "flags": list(flags_of(d)), "note": note_of(d)} for d in descriptions],
        "character_glossary": character_glossary or [],
        "token_usage": token_usage or [],
        "audio_introduction": audio_introduction or "",
        "evaluation": evaluation or {},
    }
    tmp_path = project_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, project_path)


def load_project_extras(project_path):
    """Optional parts added after format v1: audio introduction and CSA evaluation."""
    with open(project_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    evaluation = data.get("evaluation")
    return {"audio_introduction": str(data.get("audio_introduction") or ""),
            "evaluation": evaluation if isinstance(evaluation, dict) else {}}


def load_project(project_path):
    """Returns (video_path, descriptions, character_glossary, token_usage).

    Raises ValueError for files that are not valid projects. video_path may point
    to a missing file; the caller decides how to recover.
    """
    with open(project_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("format") != "omniad":
        raise ValueError("Not an Omni Describer project file.")

    video_path = data.get("video_path") or ""
    rel = data.get("video_path_relative")
    if rel:
        candidate = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(project_path)), rel))
        if os.path.exists(candidate) or not os.path.exists(video_path):
            video_path = candidate

    descriptions = []
    for item in data.get("descriptions", []):
        if isinstance(item, dict) and {"start", "end", "text"} <= item.keys():
            descriptions.append(Description(item["start"], item["end"], item["text"],
                                            item.get("level", 1), item.get("flags", ()), item.get("note", "")))
        elif isinstance(item, (list, tuple)) and len(item) == 3:  # early v1 files
            descriptions.append(Description(*item))
    descriptions.sort(key=lambda d: d[0])
    glossary = data.get("character_glossary") or []
    usage = data.get("token_usage") or []
    return video_path, descriptions, glossary if isinstance(glossary, list) else [], usage if isinstance(usage, list) else []


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    vid = os.path.join(d, "film.mp4")
    open(vid, "wb").close()
    proj = os.path.join(d, "film" + PROJECT_EXTENSION)
    save_project(proj, vid, [Description(2.0, 4.5, "Sam opens a door.", 2, ("over_budget",), "check door"), (1.0, 1.5, "Night.")], [{"id": "sam"}], [{"total_tokens": 3}])
    v, descs, g, u = load_project(proj)
    assert os.path.samefile(v, vid)
    assert descs[0] == (1.0, 1.5, "Night.") and len(descs) == 2
    assert descs[1].level == 2 and descs[1].flags == ("over_budget",) and descs[0].level == 1
    assert descs[1].note == "check door" and descs[0].note == ""
    assert g == [{"id": "sam"}] and u == [{"total_tokens": 3}]
    assert load_project_extras(proj) == {"audio_introduction": "", "evaluation": {}}
    save_project(proj, vid, descs, g, u, "Sam, a tall man.", {"ratings": {"style": 4}})
    assert load_project_extras(proj) == {"audio_introduction": "Sam, a tall man.", "evaluation": {"ratings": {"style": 4}}}
    print("project_model self-check OK")
