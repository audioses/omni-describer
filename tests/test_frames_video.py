"""Frames backend: segments, frame times, reply parsing, partial results (no ffmpeg, no network)."""
import importlib.util
import json
import sys
import types
import unittest
from unittest import mock

if importlib.util.find_spec("wx") is None:  # i18n_setup needs wx; gettext passthrough is enough here
    _i18n = types.ModuleType("audio_describer.i18n_setup")
    _i18n._ = lambda s: s
    sys.modules.setdefault("audio_describer.i18n_setup", _i18n)

try:
    from audio_describer.core import frames_video
    from audio_describer.core.gemini_helpers import GeminiAPIError
except ImportError:  # app dependencies missing
    frames_video = None


@unittest.skipIf(frames_video is None, "app dependencies not installed")
class FramesVideoTest(unittest.TestCase):
    def test_segments_cover_the_video(self):
        segs = frames_video._segments(60)
        self.assertEqual(segs[0][0], 0)
        self.assertEqual(segs[-1][1], 60)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(segs, segs[1:])))

    def test_frame_times_capped(self):
        times, _step = frames_video._frame_times(0, 100)
        self.assertLessEqual(len(times), frames_video.MAX_FRAMES)

    def test_parse_clamps_and_fits_pauses(self):
        raw = json.dumps({"audio_descriptions": [
            {"start_sec": 3, "description_text": "A door opens."},   # before the segment: clamped
            {"start_sec": 12, "description_text": "She smiles."},   # in pause 11-16: ends at 16
            {"start_sec": "x", "description_text": "bad"},
            {"start_sec": 20, "description_text": ""}],
            "character_glossary": [{"id": "woman", "description": "A woman."}, {"description": "no id"}]})
        descs, glossary = frames_video.parse_segment(raw, 10, 35, [(11, 16)])
        self.assertEqual([d[0] for d in descs], [10, 12])
        self.assertEqual(descs[1][1], 16)
        self.assertEqual([g["id"] for g in glossary], ["woman"])

    def test_parse_invalid_json(self):
        self.assertEqual(frames_video.parse_segment("not json", 0, 25, None), ([], []))

    def _describe(self, outcomes, segments=4):
        reply = json.dumps({"audio_descriptions": [{"start_sec": 1, "description_text": "x"}]})
        effects = [reply if r == "ok" else r for r in outcomes]
        with mock.patch.object(frames_video.os.path, "exists", return_value=True), \
             mock.patch.object(frames_video.video_processor, "FFMPEG_IS_AVAILABLE", True), \
             mock.patch.object(frames_video.video_processor, "get_video_duration", return_value=25 * segments), \
             mock.patch.object(frames_video.openai_vision, "settings", return_value=("u", "k", "m")), \
             mock.patch.object(frames_video.openai_vision, "chat", side_effect=effects) as chat, \
             mock.patch.object(frames_video, "_extract_frames", return_value=[(0.0, "AAAA")]), \
             mock.patch.object(frames_video.prompts, "build_unified_prompts", return_value=("s", "u")), \
             mock.patch.object(frames_video.config_model, "get_setting", return_value=False):
            return frames_video.describe_video("v.mp4"), chat

    def test_failed_segment_keeps_the_others(self):
        (descs, _g), chat = self._describe(["ok", GeminiAPIError("busy"), "ok", "ok"])
        self.assertEqual(len(descs), 3)
        self.assertEqual(chat.call_count, 4)

    def test_stops_after_failures_in_a_row_but_keeps_work(self):
        (descs, _g), chat = self._describe(["ok", GeminiAPIError("a"), GeminiAPIError("b"), "ok"])
        self.assertEqual(len(descs), 1)
        self.assertEqual(chat.call_count, 3)

    def test_first_segment_error_is_shown(self):
        with self.assertRaises(GeminiAPIError):
            self._describe([GeminiAPIError("bad key")])


if __name__ == "__main__":
    unittest.main()
