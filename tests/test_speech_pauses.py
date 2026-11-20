"""Silero VAD frame probabilities -> pauses without speech (pure helper, no model needed)."""
import unittest

from audio_describer.core import timing


class SpeechPausesTest(unittest.TestCase):
    def test_windows_between_speech_are_padded(self):
        # 10 frames of 0.5 s: speech in frames 4-5 only.
        probs = [0.1] * 4 + [0.9] * 2 + [0.1] * 4
        self.assertEqual(timing.speech_free_windows(probs, 0.5, duration=5.0, min_gap=0.8, pad=0.1),
                         [(0.0, 1.9), (3.1, 5.0)])

    def test_short_gaps_dropped(self):
        probs = [0.9, 0.1, 0.9]
        self.assertEqual(timing.speech_free_windows(probs, 0.5, min_gap=0.8), [])

    def test_no_speech_is_one_window(self):
        self.assertEqual(timing.speech_free_windows([0.0] * 4, 0.5, duration=2.0), [(0.0, 2.0)])

    def test_format_mmss(self):
        self.assertEqual([timing.format_mmss(s) for s in (0, 5.25, 75.04, 3725)],
                         ["00:00.0", "00:05.2", "01:15.0", "62:05.0"])


if __name__ == "__main__":
    unittest.main()
