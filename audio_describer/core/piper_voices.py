# audio_describer/core/piper_voices.py
"""Local neural voices (Piper models run by sherpa-onnx): free, offline, multilingual.

Optional, like Silero VAD: needs the sherpa-onnx package. A voice (~20 MB) is downloaded
once from the pinned sherpa-onnx release, checked against its SHA-256, then extracted to
the per-user folder. Used to read translated dialogue with a voice matching the speaker.
"""
import hashlib
import os
import tarfile
import threading
import urllib.request
import wave

from .. import config
from ..utils.logger import app_logger
from .piper_catalog import BASE_URL, VOICES

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
# Voices whose speaker's gender is known; other catalogue voices can still be picked in Settings.
DEFAULTS = {
    "en": {"female": "en_US-amy-medium", "male": "en_US-ryan-medium"},
    "fr": {"female": "fr_FR-siwis-medium", "male": "fr_FR-tom-medium"},
    "it": {"female": "it_IT-paola-medium", "male": "it_IT-riccardo-x_low"},
    "ru": {"female": "ru_RU-irina-medium", "male": "ru_RU-dmitri-medium"},
    "es": {"female": "es_AR-daniela-high", "male": "es_ES-davefx-medium"},
    "uk": {"female": "uk_UA-lada-x_low"},
    "pt": {"male": "pt_BR-faber-medium"},
    "ar": {"male": "ar_JO-kareem-medium"},
}

_engines = {}
_lock = threading.Lock()


def available():
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def voices_for(lang):
    """[(voice id, size MB, sha256)] for a language code such as 'fr' or 'pt_BR'."""
    return VOICES.get((lang or "en").split("_")[0].split("-")[0].lower(), [])


def default_voice(lang, gender):
    return DEFAULTS.get((lang or "en").split("_")[0].lower(), {}).get(gender, "")


def _entry(voice_id):
    return next((v for items in VOICES.values() for v in items if v[0] == voice_id), None)


def _folder():
    base = os.path.join(os.environ["LOCALAPPDATA"], "OmniDescriber") if os.environ.get("LOCALAPPDATA") else config.get_app_data_dir()
    folder = os.path.join(base, "piper")
    os.makedirs(folder, exist_ok=True)
    return folder


def _voice_dir(voice_id):
    return os.path.join(_folder(), f"vits-piper-{voice_id}-int8")


def is_installed(voice_id):
    d = _voice_dir(voice_id)
    return os.path.exists(os.path.join(d, "tokens.txt")) and any(f.endswith(".onnx") for f in os.listdir(d))


def download(voice_id, progress=None):
    """Download and unpack a catalogue voice once. progress(percent) is called while downloading."""
    entry = _entry(voice_id)
    if not entry:
        raise ValueError(f"Unknown Piper voice: {voice_id}")
    if is_installed(voice_id):
        return
    name = f"vits-piper-{voice_id}-int8.tar.bz2"
    tmp = os.path.join(_folder(), name + ".part")
    app_logger.info(f"Downloading Piper voice {voice_id} ({entry[1]} MB, once).")
    h, done = hashlib.sha256(), 0
    with urllib.request.urlopen(BASE_URL + name, timeout=60) as response, open(tmp, "wb") as f:
        total = int(response.headers.get("Content-Length") or 0)
        while True:
            block = response.read(1 << 16)
            if not block:
                break
            done += len(block)
            if done > MAX_ARCHIVE_BYTES:
                raise ValueError("Piper voice archive too large")
            h.update(block)
            f.write(block)
            if progress and total:
                progress(int(100 * done / total))
    try:
        if h.hexdigest() != entry[2]:
            raise ValueError("Piper voice checksum mismatch")
        with tarfile.open(tmp, "r:bz2") as archive:
            archive.extractall(_folder(), filter="data")  # no absolute paths, links or '..'
    finally:
        os.remove(tmp)


def _engine(voice_id):
    with _lock:
        if voice_id not in _engines:
            import sherpa_onnx
            d = _voice_dir(voice_id)
            model = next(f for f in os.listdir(d) if f.endswith(".onnx"))
            cfg = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=os.path.join(d, model), tokens=os.path.join(d, "tokens.txt"),
                        data_dir=os.path.join(d, "espeak-ng-data")),
                    num_threads=2),
                max_num_sentences=1)
            _engines[voice_id] = sherpa_onnx.OfflineTts(cfg)
        return _engines[voice_id]


def synthesize(voice_id, text, output_wav_path, speed=1.0):
    """Speak text with an installed voice into a 16-bit mono WAV. True on success."""
    if not (available() and is_installed(voice_id)):
        return False
    import numpy as np
    audio = _engine(voice_id).generate(text, sid=0, speed=max(0.5, min(float(speed), 3.0)))
    if not len(audio.samples):
        return False
    pcm = (np.clip(np.asarray(audio.samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(output_wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.sample_rate)
        w.writeframes(pcm)
    return True
