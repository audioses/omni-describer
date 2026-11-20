"""One-time conversion of the saved OneCore speech rate (the old code spoke at 2 x the saved percent)."""
import unittest
from unittest import mock

from audio_describer.models import config_model


def load(settings):
    with mock.patch.object(config_model, "save_settings", return_value=True) as save:
        result = config_model._apply_defaults_and_cleanup(dict(settings))
    return result, save.called


class OneCoreRateMigrationTest(unittest.TestCase):
    def test_existing_onecore_user_keeps_speed(self):
        settings, saved = load({"tts_engine": "onecore", "onecore_voice_rate_percent": 150})
        self.assertEqual(settings["onecore_voice_rate_percent"], 300)  # old 1.5 x 2 = 3.0x, now 300%
        self.assertTrue(settings["onecore_rate_migrated"])
        self.assertTrue(saved)

    def test_runs_only_once(self):
        settings, _ = load({"tts_engine": "onecore", "onecore_voice_rate_percent": 100})
        again, saved = load(settings)
        self.assertEqual(again["onecore_voice_rate_percent"], 200)
        self.assertFalse(saved)

    def test_clamped_to_onecore_maximum(self):
        settings, _ = load({"tts_engine": "onecore", "onecore_voice_rate_percent": 400})
        self.assertEqual(settings["onecore_voice_rate_percent"], config_model.ONECORE_RATE_MAX_PERCENT)

    def test_other_engines_only_marked(self):
        settings, _ = load({"tts_engine": "sapi5", "onecore_voice_rate_percent": 100})
        self.assertEqual(settings["onecore_voice_rate_percent"], 100)
        self.assertTrue(settings["onecore_rate_migrated"])

    def test_new_install_not_converted(self):
        settings, _ = load(dict(config_model.DEFAULT_SETTINGS, tts_engine="onecore"))
        self.assertEqual(settings["onecore_voice_rate_percent"], 100)


if __name__ == "__main__":
    unittest.main()
