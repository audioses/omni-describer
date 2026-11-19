# audio_describer/cli.py
"""Command line and batch mode (audit P3).

    python -m audio_describer.cli film.mp4 episodes\\ --lang fr --export vtt,mp4,project --out out\\
    OmniDescriber.exe --cli inbox\\ --watch --export mkv      (compiled app)

Videos get the chosen exports; images and PDFs get a .txt description. Uses the same
settings and API key as the app. A folder argument processes every supported file in it;
with --watch, new files dropped in the folders are processed as they arrive."""
import argparse
import os
import sys
import time

from .core import audio_describer, timing, script_export
from .models import config_model, project_model

AV_EXPORTS = {"mkv": ("_AD.mkv", "mkv_ad"), "mp4": ("_AD.mp4", "mp4_ad"),
              "mp3": ("_described_audio.mp3", "mp3_ducked"), "m4a": ("_AD_track.m4a", "ad_only")}
DENSITY_LEVELS = {"low": 1, "medium": 2, "high": 3}
WATCH_POLL_SEC = 5

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg")
TEXT_EXPORTS = {"vtt": (".descriptions.vtt", timing.to_webvtt), "srt": ("_descriptions.srt", timing.to_srt),
                "txt": ("_descriptions.txt", timing.to_text_lines),
                "dapt": (".dapt.ttml", lambda d: script_export.to_dapt(d, config_model.get_setting("description_language") or "en"))}
ALL_EXPORTS = sorted(TEXT_EXPORTS) + sorted(AV_EXPORTS) + ["docx", "project"]


def collect_inputs(paths):
    supported = VIDEO_EXTENSIONS + audio_describer.IMAGE_EXTENSIONS + audio_describer.DOCUMENT_EXTENSIONS
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(supported))
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"Not found: {p}", file=sys.stderr)
    return files


def process(path, exports, out_dir, prompt, density=3, extended=False, credit=True):
    base = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
    if not path.lower().endswith(VIDEO_EXTENSIONS):
        text = audio_describer.describe_file(path, prompt)  # before open(): no empty file on failure
        with open(base + "_description.txt", "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return [base + "_description.txt"]
    descriptions, glossary, usage = audio_describer.describe_video(path, prompt, lambda m: print("  " + m))
    if not descriptions:
        raise RuntimeError("no descriptions were generated")
    written = []
    played = timing.filter_by_level(descriptions, density)  # the project keeps every level
    for name in exports:
        if name == "project":
            target = base + project_model.PROJECT_EXTENSION
            project_model.save_project(target, os.path.abspath(path), descriptions, glossary, usage)
        elif name == "docx":
            target = base + "_production_script.docx"
            script_export.write_docx(target, os.path.basename(base), script_export.production_rows(played))
        elif name in AV_EXPORTS:
            from .ui import player_export  # TTS + ffmpeg mixing
            suffix, fmt = AV_EXPORTS[name]
            target = base + suffix
            for note in player_export.export_av(path, played, fmt, target, extended=extended, credit=credit):
                print("  note: " + note)
        else:
            suffix, render = TEXT_EXPORTS[name]
            target = base + suffix
            with open(target, "w", encoding="utf-8") as f:
                f.write(render(played))
        written.append(target)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(prog="omni-describer", description="Generate audio descriptions without the interface.")
    parser.add_argument("inputs", nargs="+", help="videos, images, PDFs or folders")
    parser.add_argument("--lang", choices=sorted(timing.LANGUAGE_NAMES), help="description language (default: app setting)")
    parser.add_argument("--export", default="vtt,project", help="comma list of: " + ", ".join(ALL_EXPORTS))
    parser.add_argument("--density", choices=sorted(DENSITY_LEVELS), default="high",
                        help="descriptions kept in the exports (low = essential only); the project keeps all")
    parser.add_argument("--extended", action="store_true",
                        help="audio/video exports: pause the picture when a description is longer than its pause")
    parser.add_argument("--no-credit", action="store_true", help="audio/video exports: no spoken AI credit at the end")
    parser.add_argument("--watch", action="store_true", help="keep running and process new files added to the folders")
    parser.add_argument("--out", default=".", help="output folder (default: current folder)")
    parser.add_argument("--prompt", default="", help="extra instructions for the AI")
    args = parser.parse_args(argv)

    exports = [e.strip() for e in args.export.split(",") if e.strip()]
    unknown = [e for e in exports if e not in ALL_EXPORTS]
    if unknown:
        parser.error("unknown export: " + ", ".join(unknown))
    if config_model.app_settings is None:
        config_model.app_settings = config_model.load_settings() or dict(config_model.DEFAULT_SETTINGS)
    if args.lang:
        config_model.app_settings["description_language"] = args.lang  # this run only, not saved
    os.makedirs(args.out, exist_ok=True)

    density = DENSITY_LEVELS[args.density]

    def run(files):
        failures = 0
        for i, path in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {path}")
            try:
                for target in process(path, exports, args.out, args.prompt, density, args.extended, not args.no_credit):
                    print(f"  -> {target}")
            except Exception as e:
                failures += 1
                print(f"  FAILED: {e}", file=sys.stderr)
        return failures

    files = collect_inputs(args.inputs)
    failures = run(files)
    print(f"Done: {len(files) - failures} succeeded, {failures} failed.")
    if args.watch:
        watch([p for p in args.inputs if os.path.isdir(p)], set(files), run)
    return 1 if failures or not files else 0


def new_stable_files(folders, seen, sizes):
    """Files not seen before whose size did not change since the previous poll (copy finished)."""
    ready = []
    for path in collect_inputs(folders):
        if path in seen:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:  # removed or renamed since the folder was listed
            sizes.pop(path, None)
            continue
        if sizes.get(path) == size:
            ready.append(path)
            seen.add(path)
        sizes[path] = size
    return ready


def watch(folders, seen, run, poll=WATCH_POLL_SEC):
    """Watched folder (audit §10): process each new file once its copy is complete. Ctrl+C stops."""
    if not folders:
        print("--watch needs at least one folder.", file=sys.stderr)
        return
    print(f"Watching {', '.join(folders)}. Press Ctrl+C to stop.")
    sizes = {}
    try:
        while True:
            time.sleep(poll)
            ready = new_stable_files(folders, seen, sizes)
            if ready:
                run(ready)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    sys.exit(main())
