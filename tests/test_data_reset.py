"""Reset of settings/presets: defaults restored, API keys kept on request, a backup made first."""
import json
import os
import tempfile
import unittest
from unittest import mock

from audio_describer.models import config_model, data_reset, prompt_model


class DataResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.patches = [
            mock.patch.object(config_model, "APP_DATA_DIR", d),
            mock.patch.object(config_model, "CONFIG_FILE_PATH", os.path.join(d, "settings.dat2")),
            mock.patch.object(config_model, "_obfuscate", lambda s: s.encode("utf-8")),  # no DPAPI in tests
            mock.patch.object(config_model, "app_settings", {"user_gemini_api_key": "k1", "ollama_model": "llava"}),
            mock.patch.object(prompt_model, "PROMPTS_FILE_PATH", os.path.join(d, "prompts.json")),
        ]
        for p in self.patches:
            p.start()
        with open(config_model.CONFIG_FILE_PATH, "w") as f:
            f.write("old")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _backups(self):
        root = os.path.join(self.tmp.name, "backups")
        return [os.path.join(dp, f) for dp, _, files in os.walk(root) for f in files]

    def test_settings_reset_keeps_keys_and_backs_up(self):
        data_reset.reset_settings(keep_api_keys=True)
        self.assertEqual(config_model.app_settings["user_gemini_api_key"], "k1")
        self.assertEqual(config_model.app_settings["ollama_model"], config_model.DEFAULT_SETTINGS["ollama_model"])
        self.assertTrue(any(p.endswith("settings.dat2") for p in self._backups()))

    def test_settings_reset_can_forget_keys(self):
        data_reset.reset_settings(keep_api_keys=False)
        self.assertEqual(config_model.app_settings["user_gemini_api_key"], "")

    def test_prompts_reset_restores_defaults(self):
        with mock.patch.dict(prompt_model._prompts_cache, {"fr": [{"name": "mine", "prompt": "x"}]}, clear=True):
            data_reset.reset_prompts()
            with open(prompt_model.PROMPTS_FILE_PATH, encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(saved["fr"], prompt_model.DEFAULT_PROMPTS["fr"])


if __name__ == "__main__":
    unittest.main()
