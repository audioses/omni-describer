# audio_describer/core/vad.py
"""Speech detection with Silero VAD (audit §10.2): finds stretches without speech, even
under music, so descriptions can go over music without covering voices.

Optional: needs onnxruntime + numpy. The 2 MB model is downloaded once from a pinned
release and checked against its SHA-256 (no binary in the repository). Any failure
returns None and the prompt simply has no pause hints (the behaviour without this module).
"""
import hashlib
import os
import subprocess
import urllib.request

from .. import app_state, config
from ..utils.logger import app_logger
from ..utils.system_utils import get_ffmpeg_path

MODEL_URL = "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
MODEL_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
MAX_MODEL_BYTES = 10 * 1024 * 1024  # the real file is 2.2 MB; the hash check rejects anything else
SAMPLE_RATE = 16000
FRAME = 512          # samples per inference at 16 kHz (32 ms)
CONTEXT = 64         # Silero v5 needs the last 64 samples of the previous frame
FRAME_SEC = FRAME / SAMPLE_RATE


def available():
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def model_path():
    # Per-user folder (like the logs): the app folder may be read-only under Program Files.
    folder = os.path.join(os.environ["LOCALAPPDATA"], "OmniDescriber") if os.environ.get("LOCALAPPDATA") else config.get_app_data_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "silero_vad_v5.onnx")
    if os.path.exists(path) and _sha256(path) == MODEL_SHA256:
        return path
    tmp = path + ".part"
    app_logger.info("Downloading the Silero VAD model (2 MB, once).")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response, open(tmp, "wb") as f:  # never hang generation
        f.write(response.read(MAX_MODEL_BYTES + 1))
    if _sha256(tmp) != MODEL_SHA256:
        os.remove(tmp)
        raise ValueError("Silero VAD model checksum mismatch")
    os.replace(tmp, path)
    return path


def speech_probabilities(media_path):
    """Speech probability for each 32 ms frame of the soundtrack."""
    import numpy as np
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = options.intra_op_num_threads = 1
    session = ort.InferenceSession(model_path(), sess_options=options, providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, CONTEXT), dtype=np.float32)
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    cmd = [get_ffmpeg_path("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", "error", "-i", media_path,
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    probs = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags) as proc:
        while True:
            if app_state.shutdown_event.is_set():  # the app is closing
                proc.kill()
                raise RuntimeError("Speech detection stopped: the app is closing.")
            raw = proc.stdout.read(FRAME * 2)
            if len(raw) < FRAME * 2:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)[None, :] / 32768.0
            x = np.concatenate([context, x], axis=1)
            out, state = session.run(None, {"input": x, "state": state, "sr": sr})
            context = x[:, -CONTEXT:]
            probs.append(float(out[0][0]))
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg could not decode the soundtrack")
    return probs


def detect_speech_free_windows(media_path, duration, min_gap):
    """[(start, end), ...] without speech, or None when Silero cannot run."""
    if not available():
        return None
    from . import timing
    try:
        probs = speech_probabilities(media_path)
    except Exception as e:
        app_logger.warning(f"Silero VAD unavailable, no speech pause hints: {e}")
        return None
    windows = timing.speech_free_windows(probs, FRAME_SEC, duration, min_gap=min_gap)
    app_logger.info(f"Silero VAD: {len(windows)} windows without speech")
    return windows
