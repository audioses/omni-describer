# audio_describer/ui/player_export.py
"""Exports: text (SRT, WebVTT descriptions, TXT) and audio/video built with FFmpeg.

Audio/video exports render one AD-only track (TTS clips placed at their start
times, streamed to a WAV with the stdlib `wave` module: no whole-film audio in
RAM), then a single FFmpeg graph ducks the programme with ramps, mixes, and
normalises loudness (EBU R128). The MKV export keeps every original audio and
subtitle track and adds the AD mix as a separate track tagged visual_impaired
(WCAG G78, EN 301 549 §7.2.3).
"""
from ..i18n_setup import _ as gettext_
import wx
import os
import array
import tempfile
import threading
import wave

from ..utils.logger import app_logger
from ..core import tts_engine, video_processor, timing, script_export
from ..core.tts_engine import TTSError
from ..models import config_model
from ..utils.system_utils import run_command
from .export_options_dialog import ExportOptionsDialog
from . import call_if_open

EVT_EXPORT_DONE_ID = wx.NewIdRef()
EVT_EXPORT_DONE = wx.PyEventBinder(EVT_EXPORT_DONE_ID, 0)

FFMPEG_COMMAND = video_processor.FFMPEG_COMMAND
FFPROBE_COMMAND = video_processor.FFPROBE_COMMAND
AD_SAMPLE_RATE = 48000

# ISO 639-2 codes for the AD track language tag
_ISO639_2 = {"en": "eng", "fr": "fra", "es": "spa", "ar": "ara", "pt": "por", "it": "ita", "ru": "rus",
             "uk": "ukr", "vi": "vie", "tr": "tur", "de": "deu", "ja": "jpn", "zh": "zho", "ko": "kor",
             "nl": "nld", "pl": "pol"}


class ExportDoneEvent(wx.PyCommandEvent):
    def __init__(self, output_path="", error=None, notes=()):
        super().__init__(EVT_EXPORT_DONE_ID, 0)
        self.output_path = output_path
        self.error = error
        self.notes = list(notes or ())


def start_export_process(player_instance):
    if not player_instance.descriptions:
        wx.MessageBox(gettext_("No descriptions to export."), gettext_("Export Note"), wx.OK | wx.ICON_INFORMATION, player_instance); return

    with ExportOptionsDialog(player_instance, extended_default=getattr(player_instance, "_ad_mode", "") == "extended") as dlg:
        if dlg.ShowModal() != wx.ID_OK:
            return
        options = dlg.GetValues()

    if options['category'] == 'text':
        _handle_text_export(player_instance, options)
    elif options['category'] == 'av':
        _handle_av_export(player_instance, options)
    elif options['category'] == 'full':
        _handle_full_script_export(player_instance)


def _descriptions_for_export(player_instance, options):
    """Every level, or only those of the player's current density (CAB/AMI levels, audit §10.11)."""
    if options.get("current_density", True) and getattr(player_instance, "_playlist", None) is not None:
        return list(player_instance._playlist)
    return list(player_instance.descriptions)


def _handle_text_export(player_instance, options):
    video_basename = os.path.splitext(os.path.basename(player_instance.video_path))[0]
    file_format = options['format']
    descriptions = _descriptions_for_export(player_instance, options)

    wildcard_map = {
        'srt': (gettext_("SRT Subtitles (*.srt)|*.srt"), f"{video_basename}_descriptions.srt"),
        'vtt': (gettext_("WebVTT descriptions (*.vtt)|*.vtt"), f"{video_basename}.descriptions.vtt"),
        'txt_line': (gettext_("Text files (*.txt)|*.txt"), f"{video_basename}_descriptions_lines.txt"),
        'dapt': (gettext_("TTML DAPT scripts (*.ttml)|*.ttml"), f"{video_basename}.dapt.ttml"),
        'docx': (gettext_("Word documents (*.docx)|*.docx"), f"{video_basename}_production_script.docx"),
    }
    wildcard, suggested_file = wildcard_map.get(file_format, (gettext_("All files|*.*"), "export.txt"))

    with wx.FileDialog(player_instance, gettext_("Save Exported Text File"), wildcard=wildcard, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=suggested_file) as dlg:
        if dlg.ShowModal() == wx.ID_CANCEL: return
        pathname = dlg.GetPath()
    try:
        lang = config_model.get_setting("description_language") or config_model.get_setting("application_language") or "en"
        if file_format == 'docx':
            script_export.write_docx(pathname, video_basename, script_export.production_rows(descriptions))
        else:
            writers = {'srt': timing.to_srt, 'vtt': timing.to_webvtt, 'txt_line': timing.to_text_lines,
                       'dapt': lambda d: script_export.to_dapt(d, lang)}
            with open(pathname, 'w', encoding='utf-8') as f:
                f.write(writers[file_format](descriptions))
        wx.MessageBox(gettext_("File successfully exported to:\n%s") % pathname, gettext_("Export Successful"), wx.OK | wx.ICON_INFORMATION, player_instance)
    except IOError as e:
        wx.MessageBox(gettext_("Error saving file: %s") % e, gettext_("Export Error"), wx.OK | wx.ICON_ERROR, player_instance)


def _handle_full_script_export(player_instance):
    """WCAG 1.2.8: the AI transcribes the dialogue (one text-only answer), then it is merged with the descriptions."""
    from ..core import audio_describer
    video_basename = os.path.splitext(os.path.basename(player_instance.video_path))[0]
    with wx.FileDialog(player_instance, gettext_("Save Exported Text File"), wildcard=gettext_("Text files (*.txt)|*.txt"),
                       style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=f"{video_basename}_full_script.txt") as dlg:
        if dlg.ShowModal() == wx.ID_CANCEL:
            return
        pathname = dlg.GetPath()
    descriptions = sorted(player_instance.descriptions, key=lambda d: d[0])
    busy = wx.BusyInfo(gettext_("Transcribing the dialogue, this can take a few minutes..."), player_instance)

    def work():
        try:
            dialogue = audio_describer.transcribe_dialogue(player_instance.video_path)
            with open(pathname, "w", encoding="utf-8") as f:
                f.write(script_export.to_full_script(descriptions, dialogue))
            message, icon = gettext_("File successfully exported to:\n%s") % pathname, wx.ICON_INFORMATION
        except Exception as e:
            app_logger.error(f"Full script export failed: {e}", exc_info=True)
            message, icon = gettext_("Error saving file: %s") % e, wx.ICON_ERROR

        def done():
            nonlocal busy
            del busy
            wx.MessageBox(message, gettext_("Export"), wx.OK | icon, player_instance)
        call_if_open(player_instance, done)
    threading.Thread(target=work, daemon=True).start()


def _handle_av_export(player_instance, options):
    video_basename = os.path.splitext(os.path.basename(player_instance.video_path))[0]
    file_format_map = {
        "mkv_ad": (gettext_("Matroska video (*.mkv)|*.mkv"), f"{video_basename}_AD.mkv"),
        "mp4_ad": (gettext_("MP4 video (*.mp4)|*.mp4"), f"{video_basename}_AD.mp4"),
        "mp3_ducked": (gettext_("MP3 Audio (*.mp3)|*.mp3"), f"{video_basename}_described_audio.mp3"),
        "ad_only": (gettext_("M4A Audio (*.m4a)|*.m4a"), f"{video_basename}_AD_track.m4a"),
    }
    wildcard, suggested_file = file_format_map[options['format']]

    with wx.FileDialog(player_instance, gettext_("Save Exported File"), wildcard=wildcard, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=suggested_file) as dlg:
        if dlg.ShowModal() == wx.ID_CANCEL: return
        output_path = dlg.GetPath()

    descriptions_copy = sorted(_descriptions_for_export(player_instance, options), key=lambda d: d[0])
    if not descriptions_copy:
        wx.MessageBox(gettext_("No descriptions to export."), gettext_("Export Note"), wx.OK | wx.ICON_INFORMATION, player_instance)
        return
    player_instance.progress_dialog = wx.ProgressDialog(
        gettext_("Exporting File"), gettext_("Starting..."),
        maximum=len(descriptions_copy) + 3,
        parent=player_instance,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_ELAPSED_TIME | wx.PD_ESTIMATED_TIME | wx.PD_CAN_ABORT
    )
    thread = threading.Thread(target=_perform_av_export_thread,
                              args=(player_instance, output_path, descriptions_copy, options['format'],
                                    options.get('extended', False), options.get('credit', True)))
    thread.daemon = True
    thread.start()


# Lower pitch, same speed: a distinct second voice for on-screen text with any TTS engine (Ofcom/ADLAB).
SECOND_VOICE_FILTER = f"aresample={AD_SAMPLE_RATE},asetrate={AD_SAMPLE_RATE}*0.88,aresample={AD_SAMPLE_RATE},atempo=1.1364"


def _normalize_clip(src_wav, dst_wav, second_voice=False):
    """Any TTS output -> 48 kHz mono 16-bit PCM so clips can be streamed into one WAV."""
    af = ["-af", SECOND_VOICE_FILTER] if second_voice else []
    result = run_command([FFMPEG_COMMAND, "-y", "-i", src_wav, *af, "-ar", str(AD_SAMPLE_RATE), "-ac", "1",
                          "-c:a", "pcm_s16le", dst_wav])
    if result.returncode != 0:
        raise TTSError(gettext_("FFmpeg could not convert a synthesized clip: %s") % result.stderr[-300:])


def _write_silence(writer, frames):
    block = AD_SAMPLE_RATE
    zeros = bytes(2 * block)
    while frames > 0:
        n = min(block, frames)
        writer.writeframes(zeros[:2 * n])
        frames -= n


def _fade_out_tail(pcm_bytes, fade_frames):
    samples = array.array('h', pcm_bytes)
    n = min(fade_frames, len(samples))
    for k in range(n):
        idx = len(samples) - n + k
        samples[idx] = int(samples[idx] * (1 - (k + 1) / n))
    return samples.tobytes()

# Track title and AI credit in the description language (players show the title; CSA §II.3.C credit).
_AD_LABELS = {
    "en": ("Audio description", "Audio description generated by AI with Omni Describer, synthetic voice."),
    "fr": ("Audiodescription", "Audiodescription générée par IA avec Omni Describer, voix de synthèse."),
    "es": ("Audiodescripción", "Audiodescripción generada por IA con Omni Describer, voz sintética."),
    "ar": ("الوصف الصوتي", "وصف صوتي مولَّد بالذكاء الاصطناعي باستخدام Omni Describer، بصوت اصطناعي."),
    "pt": ("Audiodescrição", "Audiodescrição gerada por IA com o Omni Describer, voz sintética."),
    "it": ("Audiodescrizione", "Audiodescrizione generata dall'IA con Omni Describer, voce sintetica."),
    "ru": ("Тифлокомментарий", "Тифлокомментарий создан ИИ в Omni Describer, синтезированный голос."),
    "uk": ("Аудіоопис", "Аудіоопис створено ШІ в Omni Describer, синтезований голос."),
    "vi": ("Mô tả âm thanh", "Mô tả âm thanh do AI tạo bằng Omni Describer, giọng nói tổng hợp."),
    "tr": ("Sesli betimleme", "Sesli betimleme Omni Describer ile yapay zekâ tarafından üretildi, yapay ses."),
    "de": ("Audiodeskription", "Audiodeskription mit KI von Omni Describer erstellt, synthetische Stimme."),
    "ja": ("音声解説", "音声解説は Omni Describer の AI が生成し、合成音声で読み上げています。"),
    "zh": ("口述影像", "口述影像由 Omni Describer 的人工智能生成，使用合成语音。"),
    "ko": ("화면 해설", "화면 해설은 Omni Describer의 AI가 생성했으며 합성 음성입니다."),
    "nl": ("Audiodescriptie", "Audiodescriptie gemaakt door AI met Omni Describer, synthetische stem."),
    "pl": ("Audiodeskrypcja", "Audiodeskrypcja wygenerowana przez AI w Omni Describer, głos syntetyczny."),
}


def _description_language():
    return (config_model.get_setting("description_language")
            or config_model.get_setting("application_language") or "en").lower()


def ad_labels(lang=None):
    """(track title, spoken/metadata credit) in the description language, English if unknown."""
    lang = (lang or _description_language()).split("_")[0]
    return _AD_LABELS.get(lang, _AD_LABELS["en"])


def _synthesize_clip(text, index, temp_files, second_voice=False, gender=None):
    raw = os.path.join(tempfile.gettempdir(), f"desc_{index}_{os.urandom(4).hex()}.wav")
    norm = raw[:-4] + "_48k.wav"
    temp_files += [raw, norm]
    if gender and tts_engine.synthesize_with_gender(text, raw, gender):
        second_voice = False  # already a distinct voice: no pitch shift
    elif not tts_engine.synthesize_description_to_wav(text, raw):
        raise TTSError(gettext_("Failed to synthesize description #%d.") % (index + 1))
    _normalize_clip(raw, norm, second_voice)
    with wave.open(norm, 'rb') as clip:
        frames = clip.getnframes()
    return norm, frames


def _build_ad_track(descriptions, ad_wav_path, progress, temp_files, extended=False, credit=None, programme_sec=0.0):
    """Synthesize every description and stream them into one AD-only WAV.

    Returns (speech intervals in output time, freezes, cut descriptions as 1-based numbers).
    Not extended: a clip longer than the gap to the next one is cut with a short fade (B10).
    Extended: the programme is frozen for the overflow instead (timing.extended_freezes).
    credit: text spoken after the last description, near the end of the programme."""
    second_voice = bool(config_model.get_setting("on_screen_text_second_voice"))
    clips = []
    for i, d in enumerate(descriptions):
        if progress(i + 1, gettext_("Synthesizing description %d of %d...") % (i + 1, len(descriptions))) is False:
            raise InterruptedError("User cancelled.")
        clips.append(_synthesize_clip(d[2], i, temp_files, second_voice and timing.is_read_text(d),
                                      timing.voice_gender(d)))
    freezes = timing.extended_freezes(descriptions, [n / AD_SAMPLE_RATE for _p, n in clips]) if extended else []

    intervals, cut, cursor = [], [], 0
    with wave.open(ad_wav_path, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(AD_SAMPLE_RATE)

        def place(pcm, at_sec):
            nonlocal cursor
            start_f = max(int(at_sec * AD_SAMPLE_RATE), cursor)
            _write_silence(writer, start_f - cursor)
            writer.writeframes(pcm)
            cursor = start_f + len(pcm) // 2
            intervals.append((start_f / AD_SAMPLE_RATE, cursor / AD_SAMPLE_RATE))

        for i, (d, (path, _frames)) in enumerate(zip(descriptions, clips)):
            with wave.open(path, 'rb') as clip:
                pcm = clip.readframes(clip.getnframes())
            start_out = timing.shift_for_freezes(d[0], freezes)
            if not extended and i + 1 < len(descriptions):
                limit = int(descriptions[i + 1][0] * AD_SAMPLE_RATE) - max(int(d[0] * AD_SAMPLE_RATE), cursor)
                if 0 < limit < len(pcm) // 2:
                    app_logger.warning(f"Export: description {i + 1} cut to fit before the next one.")
                    cut.append(i + 1)
                    pcm = _fade_out_tail(pcm[:2 * limit], int(0.03 * AD_SAMPLE_RATE))
            place(pcm, start_out)

        if credit:
            path, frames = _synthesize_clip(credit, len(descriptions), temp_files)
            with wave.open(path, 'rb') as clip:
                pcm = clip.readframes(clip.getnframes())
            end_out = timing.shift_for_freezes(programme_sec, freezes) if programme_sec else 0.0
            place(pcm, max(cursor / AD_SAMPLE_RATE + 1.0, end_out - frames / AD_SAMPLE_RATE - 1.0))
    return intervals, freezes, cut


def _probe(video_path, select, entries):
    result = run_command([FFPROBE_COMMAND, "-v", "error", "-select_streams", select, "-show_entries",
                          f"stream={entries}", "-of", "csv=p=0", video_path])
    return [l.strip() for l in result.stdout.splitlines() if l.strip()] if result.returncode == 0 else []


def _count_audio_streams(video_path):
    return len(_probe(video_path, "a", "index"))


def _frame_rate(video_path):
    for value in _probe(video_path, "v:0", "avg_frame_rate") + _probe(video_path, "v:0", "r_frame_rate"):
        num, _sep, den = value.partition("/")
        try:
            rate = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 240:
            return value
    return "25"


def _mix_filter_script(intervals, has_programme_audio, freezes=(), surround=False, video=True):
    """The FFmpeg filtergraph, written to a file by the caller (hundreds of intervals exceed the command-line limit).

    freezes: extended AD, the programme is shifted (video frames repeated, silence inserted).
    surround: a 5.1 programme keeps its layout and the AD goes to the centre channel (Ofcom 4.18, CAB)."""
    level = config_model.get_setting("mp3_audio_ducking_level_db")
    attenuation_db = abs(float(level if level is not None else 9))
    voice_gain_db = float(config_model.get_setting("ad_voice_gain_db") or 0)
    layout = "5.1" if surround else "stereo"
    shift = ""
    lines = []
    if freezes:
        expr = timing.freeze_offset_expr(freezes)
        shift = f",asetpts='PTS+({expr})/TB',aresample=async=1:min_hard_comp=0.01:first_pts=0"
        if video:
            lines.append(f"[0:v:0]setpts='PTS+({expr})/TB',fps={{fps}}[v]")
    to_layout = "pan=5.1|FC=c0" if surround else f"aformat=sample_rates={AD_SAMPLE_RATE}:channel_layouts=stereo"
    ad = f"[1:a]volume={voice_gain_db}dB,{to_layout}[ad]"
    if has_programme_audio:
        expr = timing.ducking_volume_expr(intervals, attenuation_db)
        lines += [f"[0:a:0]aformat=sample_rates={AD_SAMPLE_RATE}:channel_layouts={layout}{shift},"
                  f"volume=eval=frame:volume='{expr}'[ducked]", ad,
                  "[ducked][ad]amix=inputs=2:duration=longest:normalize=0,loudnorm=I=-23:TP=-2:LRA=11[mix]"]
    else:
        lines += [ad, "[ad]loudnorm=I=-23:TP=-2:LRA=11[mix]"]
    return ";\n".join(lines)


def _perform_av_export_thread(player_instance, output_path, descriptions, fmt, extended=False, credit=True):
    cancelled = threading.Event()

    def update_dialog(step, message):  # UI thread only: wx calls are not thread-safe
        dialog = player_instance.progress_dialog
        if not dialog:
            cancelled.set()
            return
        dialog.Update(step, message)
        if dialog.WasCancelled():
            cancelled.set()

    def progress(step, message):
        if not player_instance or player_instance._is_closing:
            cancelled.set()  # player closed: stop ffmpeg instead of exporting for nobody
        else:
            call_if_open(player_instance, update_dialog, step, message)
        return not cancelled.is_set()
    try:
        notes = export_av(player_instance.video_path, descriptions, fmt, output_path, progress,
                          extended=extended, credit=credit)
        call_if_open(player_instance, update_dialog, len(descriptions) + 3, gettext_("Export Complete."))
        call_if_open(player_instance, wx.PostEvent, player_instance, ExportDoneEvent(output_path=output_path, notes=notes))
    except Exception as e:
        app_logger.error(f"Error during export thread: {e}", exc_info=True)
        call_if_open(player_instance, wx.PostEvent, player_instance, ExportDoneEvent(error=e))


def export_av(video_path, descriptions, fmt, output_path, progress=lambda step, message: True,
              extended=False, credit=True):
    """Synthesize and mix (no UI; used by the player and the command line).
    fmt: mkv_ad | mp4_ad | mp3_ducked | ad_only. progress returning False cancels.
    extended: freeze the picture where a description overflows its pause (re-encodes the video;
    not for ad_only, which must stay in sync with the original). credit: spoken AI credit at the end.
    Returns notes for the user (descriptions cut, subtitles not kept...)."""
    temp_files, notes = [], []
    try:
        if not video_processor.FFMPEG_IS_AVAILABLE:
            raise TTSError(gettext_("FFmpeg is required for audio and video export."))
        extended = extended and fmt != "ad_only"
        title, credit_text = ad_labels()
        programme_sec = video_processor.get_video_duration(video_path) if credit else 0.0

        ad_wav = os.path.join(tempfile.gettempdir(), f"ad_track_{os.urandom(4).hex()}.wav")
        temp_files.append(ad_wav)
        intervals, freezes, cut = _build_ad_track(descriptions, ad_wav, progress, temp_files, extended,
                                                  credit_text if credit else None, programme_sec)
        if cut:
            notes.append(gettext_("%(n)d descriptions were too long for their pause and were cut (numbers: %(list)s). "
                                  "Shorten them, or export again with the extended description option.")
                         % {"n": len(cut), "list": ", ".join(map(str, cut[:20])) + ("..." if len(cut) > 20 else "")})
        if freezes:
            notes.append(gettext_("The picture is paused %(n)d times (%(sec).1f seconds in total) so that long "
                                  "descriptions are read in full.") % {"n": len(freezes), "sec": sum(f for _p, f in freezes)})

        progress(len(descriptions) + 1, gettext_("Mixing audio tracks..."))
        lang3 = _ISO639_2.get(_description_language().split("_")[0], "und")
        meta = ["-metadata", f"comment={credit_text}"]
        n_subs = 0

        if fmt == "ad_only":
            commands = [[FFMPEG_COMMAND, "-y", "-i", ad_wav, "-c:a", "aac", "-b:a", "160k", *meta,
                         "-metadata:s:a:0", f"language={lang3}", "-metadata:s:a:0", f"title={title}", output_path]]
        else:
            n_audio = _count_audio_streams(video_path)
            n_subs = len(_probe(video_path, "s", "index"))
            channels = (_probe(video_path, "a:0", "channels") or ["2"])[0]
            surround = fmt != "mp3_ducked" and channels == "6"
            script = os.path.join(tempfile.gettempdir(), f"ad_mix_{os.urandom(4).hex()}.txt")
            temp_files.append(script)
            graph = _mix_filter_script(intervals, n_audio > 0, freezes, surround, video=fmt != "mp3_ducked")
            if freezes:
                graph = graph.replace("{fps}", _frame_rate(video_path))
            with open(script, 'w', encoding='utf-8') as f:
                f.write(graph)
            # "-/opt file" reads the option from a file (FFmpeg 7+; -filter_complex_script is gone in 9)
            base = [FFMPEG_COMMAND, "-y", "-i", video_path, "-i", ad_wav, "-/filter_complex", script]
            track = n_audio if not freezes else 0  # extended: the mix is the only audio track
            tagged = [f"-disposition:a:{track}", "visual_impaired+default" if freezes else "visual_impaired",
                      f"-metadata:s:a:{track}", f"title={title}", f"-metadata:s:a:{track}", f"handler_name={title}",
                      f"-metadata:s:a:{track}", f"language={lang3}", *meta]
            if fmt == "mp3_ducked":
                commands = [base + ["-map", "[mix]", "-vn", "-c:a", "libmp3lame", "-b:a", "192k", *meta, output_path]]
            elif freezes:  # the picture is re-timed: re-encode it; original subtitles would be out of sync
                reencode = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k"]
                commands = [base + ["-map", "[v]", "-map", "[mix]"] + reencode + tagged
                            + (["-movflags", "+faststart"] if fmt == "mp4_ad" else []) + [output_path]]
                if n_subs:
                    notes.append(gettext_("The original subtitles were not kept: with the picture paused they would be out of sync."))
            elif fmt == "mp4_ad":  # MP4 can't hold every codec: copy first, then convert audio, then video
                tail = ["-map", "[mix]"] + tagged + ["-movflags", "+faststart", output_path]
                maps = base + ["-map", "0:v:0?", "-map", "0:a?"]
                subs = ["-map", "0:s?", "-c:s", "mov_text"]
                attempts = [["-c:v", "copy", "-c:a", "copy", f"-c:a:{n_audio}", "aac", f"-b:a:{n_audio}", "192k"],
                            ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"],
                            ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k"]]
                commands = ([maps + subs + a + tail for a in attempts] if n_subs else []) + [maps + a + tail for a in attempts]
            else:  # mkv_ad: originals kept, AD mix added as its own tagged track
                tail = ["-map", "[mix]", "-c:v", "copy", "-c:a", "copy",
                        f"-c:a:{n_audio}", "aac", f"-b:a:{n_audio}", "192k"] + tagged + [output_path]
                maps = base + ["-map", "0:v?", "-map", "0:a?"]
                # MP4 text subtitles (mov_text) can't be copied into MKV: converted to SRT instead
                commands = [maps + ["-map", "0:s?", "-c:s", "copy"] + tail, maps + ["-map", "0:s?", "-c:s", "srt"] + tail,
                            maps + tail]

        result = None
        for k, cmd in enumerate(commands):
            app_logger.info(f"Export FFmpeg: {' '.join(cmd)}")
            result = run_command(cmd)
            if result.returncode != 0 and "-/filter_complex" in cmd and "Unrecognized option" in result.stderr:
                old = ["-filter_complex_script" if c == "-/filter_complex" else c for c in cmd]  # FFmpeg < 7
                app_logger.info("Old FFmpeg: retrying with -filter_complex_script")
                result = run_command(old)
            if result.returncode == 0:
                if n_subs_dropped(fmt, freezes, commands, k, n_subs):
                    notes.append(gettext_("The original subtitles could not be copied into this format and were not kept."))
                break
            app_logger.warning(f"FFmpeg export attempt failed: {result.stderr[-1500:]}")
        if result.returncode != 0:
            raise TTSError(gettext_("FFmpeg failed to create the export:\n%s") % result.stderr[-400:])
        return notes
    finally:
        for path in temp_files:
            if os.path.exists(path):
                try: os.remove(path)
                except Exception as e:
                    app_logger.warning(f"Failed to remove temp file {path}: {e}")


def n_subs_dropped(fmt, freezes, commands, used_index, n_subs):
    """True when the command that worked is one without the subtitle streams although the source has some."""
    if not n_subs or freezes or fmt not in ("mp4_ad", "mkv_ad"):
        return False
    return "0:s?" not in commands[used_index]


def export_intro_mp3(text, output_path):
    """Audio introduction (Ofcom 4.14) as its own MP3, to publish before the programme."""
    raw = os.path.join(tempfile.gettempdir(), f"intro_{os.urandom(4).hex()}.wav")
    try:
        if not tts_engine.synthesize_description_to_wav(text, raw):
            raise TTSError(gettext_("Failed to synthesize the audio introduction."))
        result = run_command([FFMPEG_COMMAND, "-y", "-i", raw, "-c:a", "libmp3lame", "-q:a", "3",
                              "-metadata", f"title={ad_labels()[0]}", output_path])
        if result.returncode != 0:
            raise TTSError(gettext_("FFmpeg failed to create the export:\n%s") % result.stderr[-400:])
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
