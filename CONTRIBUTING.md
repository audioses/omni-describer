# Contributing to Omni Describer

Thanks for taking an interest — this started as a small free tool, and
outside contributions are genuinely welcome, especially from people who
actually use screen readers or rely on audio description day to day.

## Setup

See [README.md](README.md#option-b-run-from-source) for cloning and running
from source. Short version:

```bash
pip install -r audio_describer/requirements.txt
python run_app.py
```

You'll need your own Gemini API key (Settings → AI after launch) and FFmpeg
on your PATH or in `audio_describer/bin/`.

## Making a change

1. Fork and branch off `main`.
2. Keep changes focused — one fix or feature per PR.
3. Follow the conventions in [AGENTS.md](AGENTS.md) (logging via
   `app_logger`, user-facing strings through `_()`, no hardcoded secrets or
   binary paths).
4. Run `python -m unittest discover -s tests` (stdlib only, also run in CI),
   then run the app and exercise the flow you touched. If your change adds non-trivial logic (a parser, a
   branch with real consequences), a small `test_*.py` is appreciated but not
   required for small fixes.
5. Open a PR describing what changed and why, and how you tested it.

## Translations

Translations live in `audio_describer/locale/<lang>/LC_MESSAGES/`. To add or
update one:

1. Edit the `.po` file for your language (or copy `omni_describer.po` from
   another language to start a new one).
2. Regenerate the compiled catalog: `msgfmt omni_describer.po -o
   omni_describer.mo`.
3. Commit both the `.po` and `.mo`.

Default prompt presets are plain JSON, one file per language: `audio_describer/prompts/<lang>.json`
(a list of `{"name": ..., "prompt": ...}`). To translate them, copy `en.json` to your language code
and translate the values; no code change is needed. A language without a file gets the English presets.
The app follows the Windows display language unless one is chosen in Settings.

## Building a release executable

The main app is built with PyInstaller via `build.spec` / `build.bat`. Two
small helper executables are built separately and aren't covered by that
spec:

```bash
pyinstaller --onefile sapi32.py     # 32-bit SAPI5 TTS helper
pyinstaller --onefile updater.py    # self-update helper
```

FFmpeg and VLC are not part of the repo — place them under
`audio_describer/bin/` yourself before building if you want them bundled
into your build (see [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES) for the
licensing that applies if you redistribute a build that includes them).

## Reporting bugs

Open an issue with: what you did, what you expected, what happened instead,
and the relevant lines from `app_log.txt` (your local log file, next to the
app — never committed, safe to paste from).

## Code of conduct

Be respectful. This tool exists to help people who are blind or low-vision;
keep that audience in mind in how you discuss and prioritize changes.
