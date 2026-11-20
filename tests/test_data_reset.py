"""Settings reset: defaults restored, API keys kept on request, a backup made first."""
import os
import tempfile
import unittest
from unittest import mock

from audio_describer.models import config_model, data_reset


class DataResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        for p in (mock.patch.object(config_model, "APP_DATA_DIR", d),
                  mock.patch.object(config_model, "CONFIG_FILE_PATH", os.path.join(d, "settings.dat2")),
                  mock.patch.object(config_model, "app_settings", {"user_gemini_api_key": "k1", "gemini_temperature": 0.9})):
            p.start()
            self.addCleanup(p.stop)
        with open(config_model.CONFIG_FILE_PATH, "w") as f:
            f.write("old")

    def _backups(self):
        root = os.path.join(self.tmp.name, "backups")
        return [f for _, _, files in os.walk(root) for f in files]

    def test_settings_reset_keeps_keys_and_backs_up(self):
        data_reset.reset_settings(keep_api_keys=True)
        self.assertEqual(config_model.app_settings["user_gemini_api_key"], "k1")
        self.assertEqual(config_model.app_settings["gemini_temperature"], config_model.DEFAULT_SETTINGS["gemini_temperature"])
        self.assertIn("settings.dat2", self._backups())

    def test_settings_reset_can_forget_keys(self):
        data_reset.reset_settings(keep_api_keys=False)
        self.assertEqual(config_model.app_settings["user_gemini_api_key"], "")


if __name__ == "__main__":
    unittest.main()
