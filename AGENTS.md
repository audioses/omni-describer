# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, etc.) working
in this repository.

## What this is

Omni Describer: a Windows desktop app (wxPython) that uses Google Gemini to
generate audio descriptions for video, for blind/low-vision users. See
[README.md](README.md) for the full picture.

## Stack

- Python 3.10+, wxPython for UI, no async framework — threading + `wx.CallAfter`
  for background work (network calls, TTS, downloads).
- `google-generativeai` for Gemini calls, `yt-dlp` for YouTube, bundled/system
  FFmpeg for media processing, VLC for in-app playback.
- i18n via gettext (`.po`/`.mo`), wrapped through `_()` from `i18n_setup.py`.

## Layout

| Path | Purpose |
|---|---|
| `run_app.py` | Entry point. Two-stage crash handling — don't remove the try/except wrapping. |
| `audio_describer/core/` | Business logic: Gemini calls (`gemini_helpers.py`), video processing (`video_processor.py`), YouTube download (`youtube_downloader.py`), TTS (`tts_engine.py`). |
| `audio_describer/ui/` | wxPython windows/dialogs. One file per window/dialog. |
| `audio_describer/models/` | Settings (`config_model.py`), prompts, voices — persisted to disk as local JSON/obfuscated config, not part of the repo. |
| `audio_describer/utils/` | Logging (`logger.py`), ffmpeg/VLC path discovery (`system_utils.py`), update checking (`update_checker.py`), notification sounds (`sound_player.py`, plays directly from `audio_describer/notifs/`). |
| `audio_describer/locale/` | Translations. Don't hand-edit `.mo` files — regenerate from `.po` with `msgfmt`. |

## Conventions already in place — follow them

- User-facing strings go through `_()` (gettext), not raw string literals in
  UI code.
- Use `app_logger` (from `audio_describer/utils/logger.py`) for anything
  worth diagnosing later — not bare `print()`.
- FFmpeg/VLC lookup always goes through `system_utils.get_ffmpeg_path()` /
  the equivalent VLC discovery, which checks `audio_describer/bin/` before
  falling back to system `PATH`. Don't hardcode a binary path.
- Settings (including API keys) go through `config_model` — never hardcode a
  key or secret in source. `config.py`'s `GEMINI_API_KEY = None` is
  intentional; the real key comes from the user via Settings at runtime.

## Things to be careful about

- **Windows-only.** `winsdk`, the SAPI5 helper (`sapi32.exe`, built from
  `sapi32.py`), and `os.name == 'nt'` branches assume Windows. Don't add
  cross-platform abstractions unless you're actually implementing
  cross-platform support end to end.
- **Minimal tests only.** `tests/` holds a few stdlib `unittest` checks of
  pure helpers (no wx, run in CI): `python -m unittest discover -s tests`.
  Still manually run the app (`python run_app.py`)
  and exercise the path you changed before calling something done. If you add
  non-trivial logic, a small `test_*.py` or an `assert`-based self-check is
  welcome, not required.
- **Two separate helper executables** are built independently from the main
  app: `sapi32.py` → `sapi32.exe` (32-bit SAPI5 TTS helper) and `updater.py` →
  `updater.exe` (self-update helper, downloaded on demand). Build each with
  `pyinstaller --onefile <file>.py`; see [CONTRIBUTING.md](CONTRIBUTING.md).
- Don't reintroduce a trial/expiration check, license-key gate, or telemetry
  beyond the existing update-check call — this is a free, open-source tool by
  design. There used to be a "message of the moment" checker
  (`motm_checker.py`); it was dead code (no call sites) and has been removed
  — don't re-add it.
- Notification sounds are plain files in `audio_describer/notifs/`, played
  directly — there's no packing/encryption step. Don't reintroduce one.
- FFmpeg and VLC binaries are **not** part of this repo (see
  [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)) and shouldn't be committed —
  they're fetched locally or bundled only in release builds.

## Making changes

- Keep diffs scoped to one dialog/module where possible — the `ui/` files are
  already split one-per-window; don't merge them back together.
- If you touch a `.po` file, regenerate the matching `.mo` (`msgfmt file.po -o
  file.mo`) in the same change.
- No CI is configured. Before proposing a change, at minimum import-check the
  modified modules and manually exercise the affected UI flow.
