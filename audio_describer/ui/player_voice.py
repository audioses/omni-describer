# audio_describer/ui/player_voice.py
"""App voice in the player (audit §6.2): descriptions are synthesised ahead of time,
so their real duration is known for exact ducking and extended-AD pauses."""
import hashlib
import os
import shutil
import tempfile
import threading
import wave

from ..core import tts_engine
from ..utils.logger import app_logger


class VoiceCache:
    def __init__(self):
        self._clips = {}  # text -> (wav path, seconds)
        self._dir = tempfile.mkdtemp(prefix="omni_voice_")
        self._lock = threading.Lock()  # one synthesis at a time (SAPI/OneCore are not thread-safe)
        self._stop = threading.Event()

    def get(self, text):
        return self._clips.get(text)

    def prepare(self, items):
        """Synthesise missing clips in playback order, in the background.
        items: (text, speaker gender or None) pairs; translated lines get a matching voice."""
        todo = {t: g for t, g in items if t and t not in self._clips}
        if todo:
            threading.Thread(target=self._run, args=(todo,), daemon=True).start()

    def _run(self, texts):
        for text, gender in texts.items():
            with self._lock:
                if self._stop.is_set():
                    return
                if text in self._clips:
                    continue
                path = os.path.join(self._dir, hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] + ".wav")
                try:
                    if tts_engine.synthesize_description_to_wav(text, path, gender=gender):
                        with wave.open(path, "rb") as w:
                            self._clips[text] = (path, w.getnframes() / float(w.getframerate()))
                except Exception as e:  # the screen reader is used for this description instead
                    app_logger.warning(f"App voice: synthesis failed: {e}")

    def close(self):
        self._stop.set()
        shutil.rmtree(self._dir, ignore_errors=True)
