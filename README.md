# Omni Describer

A Windows desktop app that generates audio descriptions for video, so blind
and low-vision viewers can follow what's happening on screen. Point it at a
local video file or a YouTube URL, let Google Gemini watch and describe the
scenes, review and edit the script, then export a video with the description
track mixed in.

## The story

It all started with a love for movies. Realizing how many details in a great
scene get lost without good audio description sparked a simple question:
couldn't AI make this easier? Not just generating descriptions, but handing
full control back to the person watching. Months of work and a lot of trial
and error later, Omni Describer was the result — free from day one, because
good audio description shouldn't be locked behind a price tag.

"Omni" is Latin for "all" — the goal was never to serve one narrow purpose.
Beyond accessibility, it turned into an exploration tool: with features like
Scene Explorer and Ask More, anyone curious about a film's visual details —
critics, students, artists — could dig in a way that wasn't possible before.
A describer for everything, for everyone.

The ideas here — chunked long-form analysis, character glossaries, prompt-
driven description styles — went on to power
[studio.binclusive.io](https://studio.binclusive.io), a web platform built in
partnership with Binclusive that needs no install and no API keys. That's
where active development is now focused, and the desktop app remains in
maintenance mode, still fully working for anyone who prefers it.

Open-sourcing this now, on its one-year anniversary, so the tool that started
it all can keep going in the hands of anyone who wants to build on it. Thanks
to everyone who tested, translated, and used it along the way — see
[Acknowledgments](#acknowledgments).

## Features

- **AI-generated descriptions** — Gemini analyzes video content and writes
  scene-by-scene description text.
- **Local files or YouTube** — import a video file directly, or paste a
  YouTube URL and let the app download it.
- **Editable output** — review, rewrite, and fine-tune every generated
  description before exporting.
- **Text-to-speech** — descriptions are synthesized to audio (Windows OneCore
  voices via `winsdk`, with a 32-bit SAPI5 fallback) and mixed into the final
  export.
- **Character glossary** — recurring characters/entities get tracked
  consistently across chunks instead of being re-described from scratch.
- **Multi-language UI** — English, Turkish, and several community-contributed
  translations (see [Acknowledgments](#acknowledgments)).

## Platform support

Windows only, currently. The app depends on `winsdk` (OneCore speech
synthesis), a bundled SAPI5 helper, and Windows-specific paths throughout —
there's no macOS/Linux build path today.

## Getting started

### Option A: Download a build

Grab the latest release from the [Releases](../../releases) page — a
self-contained Windows build you can run without installing Python. It bundles
everything it needs: [yt-dlp](https://github.com/yt-dlp/yt-dlp) (which
self-updates on use), FFmpeg/ffprobe, and the VLC runtime. Nothing to install
separately.

### Option B: Run from source

**Requirements:** Python 3.10+, [FFmpeg](https://ffmpeg.org/download.html),
and [VLC](https://www.videolan.org/vlc/) (or its runtime DLLs) somewhere on
your system.

```bash
git clone <this-repo>
cd omnidescriber
pip install -r audio_describer/requirements.txt
python run_app.py
```

The app looks for `ffmpeg`/`ffprobe`/VLC in `audio_describer/bin/` first,
falling back to your system `PATH`. Drop them in `audio_describer/bin/` if
you don't want to rely on PATH.

YouTube downloads use [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the
`pip install` above installs it, so a source run needs no extra setup (the
app calls it as a subprocess and self-updates it once per run via
`yt-dlp -U`). See
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES) for the licenses that apply if
you redistribute a build with these binaries included.

### Configuration

On first launch, open **Settings → AI** and paste in your own Gemini (and
optionally OpenAI) API key. Keys are saved locally in your user data folder
(lightly obfuscated, not committed to any repo) — nothing here ships with or
requires a hardcoded key.

## Project layout

```
audio_describer/
  core/       video processing, YouTube download, Gemini + TTS integration
  ui/         wxPython windows and dialogs
  models/     settings, prompts, and voice config
  utils/      logging, ffmpeg/VLC discovery, update checking
  locale/     translations (.po/.mo)
run_app.py    entry point
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgments

This project wouldn't exist without the mentors, beta testers, translators,
and everyone who used the app and sent feedback — the full list of credits
lives in [contributors.txt](audio_describer/doc/en/contributors.txt), the same
acknowledgments shown inside the app.

And the open-source projects it stands on: Google Gemini's API, FFmpeg, VLC,
and the Python/wxPython ecosystem.

## License

MIT — see [LICENSE](LICENSE). Bundled third-party components (FFmpeg, VLC)
are **not** part of this repository and are licensed separately — see
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES) for the terms that apply to any
build that includes them.
