# Omni Describer

A Windows desktop app that generates audio descriptions for video, so blind
and low-vision viewers can follow what's happening on screen. Point it at a
local video file or a YouTube URL, let Google Gemini watch and describe the
scenes, review and edit the script, then export a video with the description
track mixed in.

> **⚠️ Only download Omni Describer from this repository's
> [Releases page](https://github.com/audioses/omni-describer/releases).**
> Copies of this project elsewhere on GitHub have been found linking to
> malware. If a "download" leads to a ZIP containing a `.cmd` file and a
> `.txt` file, do not run it.

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
- **Professional rules** — descriptions follow the main AD guidelines (Netflix,
  DCMP, ACB, CSA/Arcom, Ofcom, CAB/AMI, ADLAB), are placed in detected pauses with
  a word budget, tagged by level (1–3), and checked by a second AI review pass.
- **Accessible player** — density levels, inline / extended / on-demand modes,
  Ask More questions and a Scene Explorer about the moment you paused on, audio
  introduction, braille and large-text output.
- **Text-to-speech** — descriptions are synthesized to audio (Windows OneCore
  voices via `winsdk`, a 32-bit SAPI5 fallback, or OpenAI TTS) and mixed into
  the final export.
- **Exports** — MKV/MP4 with a tagged AD track named in the description
  language, AD-only audio, WebVTT, DAPT, DOCX and full script; optional
  extended description (the picture pauses instead of cutting a long
  description), spoken AI credit, export at the player's density, source
  subtitles kept, AD on the centre channel for 5.1. Projects save and reopen
  (File → Recent Projects); SRT/WebVTT scripts can be imported.
- **Voice control** — pronunciation dictionary, listen to a line with the
  export voice from the editor, audio introduction saved as MP3 (Shift+I).
- **Images, PDFs and your screen** — describe a picture or document (Ctrl+I),
  or the screen with a global hotkey; optional local model through Ollama.
- **Fully local option** — video descriptions can also come from a local Ollama
  vision model (free, private, slower than Gemini).
- **Voices matching the speaker** — translated dialogue is read with a woman's
  or a man's voice: an installed Windows voice, or a free local Piper voice
  (downloaded once, ~20 MB, checksum verified). Ctrl+G in the editor fixes it.
- **Character glossary** — recurring characters/entities get tracked
  consistently across chunks instead of being re-described from scratch.
- **Multi-language UI** — ten languages (en, ar, es, fr, it, pt, ru, tr, uk,
  vi) for the interface, the help and the prompt presets; presets can be
  exported and imported to share them (see [Acknowledgments](#acknowledgments)).

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

On first launch the app offers to open Google AI Studio and **Settings → AI**:
paste in your own Gemini (and optionally OpenAI) API key and press **Test key**. Keys are saved locally in your user data folder
(encrypted with Windows DPAPI for your account, not committed to any repo) — nothing here ships with or
requires a hardcoded key.

### Command line and batch mode

From a source checkout, with the API key already set in the app:

```bash
python -m audio_describer.cli film.mp4 episodes/ --lang fr --export vtt,srt,txt,project --out out/
```

Videos get the chosen exports (`project` is an `.omniad` file the app can
open); images and PDFs get a `_description.txt`. A failed file does not stop
the batch; the exit code is 1 if any file failed.

Other options: `--density low|medium|high` (descriptions kept in the
exports; the project keeps all), exports `dapt`, `docx`, `mkv`, `mp4`, `mp3`,
`m4a`, `--extended` (pause the picture for long descriptions in video/MP3
exports), `--no-credit` (no spoken AI credit at the end), and `--watch` to
keep processing new files copied into the folders.
The compiled app runs the same command line with `OmniDescriber.exe --cli ...`.

Speech detection (finding pauses under music) uses Silero VAD through
`onnxruntime`; its 2 MB model is downloaded once from a pinned release and
checked by SHA-256. Without `onnxruntime` the app falls back to ffmpeg
silence detection.

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
