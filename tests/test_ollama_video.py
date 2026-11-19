"""Local video descriptions with Ollama: parsing and clamping, Ollama and ffmpeg mocked."""
import importlib.util
import json
import sys
import threading
import types
import unittest
from unittest import mock

if importlib.util.find_spec("wx") is None:  # i18n_setup needs wx; gettext passthrough is enough here
    _i18n = types.ModuleType("audio_describer.i18n_setup")
    _i18n._ = lambda s: s
    sys.modules.setdefault("audio_describer.i18n_setup", _i18n)

try:
    from audio_describer.core import ollama_video, timing
except Exception as e:  # app dependencies (google-genai...) missing
    ollama_video, IMPORT_ERROR = None, e


class _Response:
    def __init__(self, payload):
        self._body = json.dumps({"response": json.dumps(payload)}).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@unittest.skipIf(ollama_video is None, "app dependencies not installed")
class OllamaVideoTest(unittest.TestCase):
    def test_segments_cover_video_and_snap_to_cuts(self):
        segs = ollama_video._segments(60.0, [27.0])
        self.assertEqual(segs[0], (0.0, 27.0))
        self.assertEqual(segs[-1][1], 60.0)
        self.assertTrue(all(a < b for a, b in segs))

    def test_describe_video_locally_parses_and_clamps(self):
        answers = [
            {"audio_descriptions": [
                {"start_sec": -5, "description_text": "A man opens a door.", "level": 1, "on_screen_text": False},
                {"start_sec": 12, "description_text": "Sign: EXIT", "level": 2, "on_screen_text": True},
                {"start_sec": "bad", "description_text": "dropped", "level": 1, "on_screen_text": False},
                {"start_sec": 14, "description_text": "  ", "level": 1, "on_screen_text": False}],
             "character_glossary": [{"id": "man_in_coat", "description": "a tall man in a coat", "name": None}]},
            {"audio_descriptions": [
                {"start_sec": 2, "description_text": "Sign: EXIT", "level": 2, "on_screen_text": True},
                {"start_sec": 26, "description_text": "The man sits.", "level": 3, "on_screen_text": False}]},
        ]
        sent = []

        def fake_urlopen(request, timeout):
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:11434/"))
            sent.append(json.loads(request.data))
            return _Response(answers[len(sent) - 1])

        settings = {"ollama_model": "gemma3", "enable_silence_detection": True}
        vp = ollama_video.video_processor
        with mock.patch.object(ollama_video.os.path, "exists", return_value=True), \
             mock.patch.object(vp, "FFMPEG_IS_AVAILABLE", True), \
             mock.patch.object(vp, "get_video_duration", return_value=40.0), \
             mock.patch.object(vp, "detect_quiet_windows", return_value=[(0.0, 5.0), (11.5, 20.0)]), \
             mock.patch.object(vp, "detect_shot_changes", return_value=[]), \
             mock.patch.object(ollama_video, "_extract_frames", side_effect=lambda p, a, b: [(a, "AAAA"), (a + 4, "BBBB")]), \
             mock.patch.object(ollama_video.config_model, "get_setting", side_effect=lambda k: settings.get(k)), \
             mock.patch.object(ollama_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            descs, glossary, usage = ollama_video.describe_video_locally("v.mp4", "", lambda m: None, threading.Event())

        self.assertEqual(usage, [])
        self.assertEqual(len(sent), 2)  # 0-25 s and 25-40 s
        self.assertEqual(sent[0]["images"], ["AAAA", "BBBB"])
        self.assertEqual([d[2] for d in descs], ["A man opens a door.", "Sign: EXIT", "The man sits."])  # dup removed
        first, sign, last = descs
        self.assertEqual(first[0], 0.0)            # clamped up into the segment
        self.assertEqual(first[1], 5.0)            # ends with its pause
        self.assertEqual(sign.level, 2)
        self.assertIn(timing.FLAG_ON_SCREEN_TEXT, sign.flags)
        self.assertEqual(last[0], 26.0)
        self.assertIn(timing.FLAG_OVERLAPS_SOUND, last.flags)
        self.assertEqual(glossary[0]["id"], "man_in_coat")
        self.assertIn("man_in_coat", sent[1]["prompt"])  # glossary carried to the next segment

    def test_ollama_not_running_gives_clear_error(self):
        with mock.patch.object(ollama_video.urllib.request, "urlopen",
                               side_effect=ollama_video.urllib.error.URLError("refused")):
            with self.assertRaises(ollama_video.GeminiAPIError) as ctx:
                ollama_video._ask_ollama("gemma3", "p", [])
        self.assertIn("ollama pull gemma3", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
