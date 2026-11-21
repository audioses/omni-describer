"""Default presets from prompts/<language>.json, merged once by id into the user's prompts.json."""
import json
import os
import tempfile
import unittest
from unittest import mock

from audio_describer.models import config_model, data_reset, prompt_model

EN = [{"id": "a", "name": "A", "prompt": "a text"}, {"id": "b", "name": "B", "prompt": "b text"}]
real_defaults, real_legacy = prompt_model.DEFAULT_PROMPTS, prompt_model.LEGACY_DEFAULT_IDS


class ShippedDefaultsTest(unittest.TestCase):
    def test_english_defaults_are_valid_with_unique_ids(self):
        en = prompt_model.DEFAULT_PROMPTS["en"]
        prompt_model.validate_presets(en)
        ids = [p["id"] for p in en]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(prompt_model.LEGACY_DEFAULT_IDS <= set(ids))

    def test_language_without_file_falls_back_to_english(self):
        self.assertEqual(prompt_model._get_default_prompts_for_lang("xx"), prompt_model.DEFAULT_PROMPTS["en"])


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "prompts.json")
        for p in (mock.patch.object(prompt_model, "PROMPTS_FILE_PATH", self.path),
                  mock.patch.object(prompt_model, "DEFAULT_PROMPTS", {"en": [dict(p) for p in EN]}),
                  mock.patch.object(prompt_model, "LEGACY_DEFAULT_IDS", frozenset({"a"})),
                  mock.patch.object(prompt_model, "_prompts_cache", {}),
                  mock.patch.object(config_model, "APP_DATA_DIR", self.tmp.name),
                  mock.patch.object(config_model, "CONFIG_FILE_PATH", os.path.join(self.tmp.name, "settings.dat2")),
                  mock.patch.object(config_model, "app_settings", dict(config_model.DEFAULT_SETTINGS))):
            p.start()
            self.addCleanup(p.stop)

    def _names(self):
        """Simulates an app start: reload prompts.json, return the English preset names."""
        prompt_model.load_prompts()
        return [p["name"] for p in prompt_model.get_prompts_for_language("en")]

    def test_old_file_gets_only_new_defaults_and_deleted_legacy_stays_gone(self):
        # Written by a version without ids: "A" was offered then, and the user deleted it.
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"en": [{"name": "Mine", "prompt": "x"}], "tr": [{"name": "T", "prompt": "t"}]}, f)
        self.assertEqual(self._names(), ["Mine", "B"])
        self.assertEqual(self._names(), ["Mine", "B"])  # next start: nothing added
        with open(self.path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[prompt_model.SEEN_DEFAULTS_KEY], ["a", "b"])
        self.assertEqual(saved["tr"], [{"name": "T", "prompt": "t"}])

    def test_deleted_or_renamed_default_does_not_come_back(self):
        self.assertEqual(self._names(), ["A", "B"])  # first start, no file
        prompt_model.set_prompts_for_language("en", [{"id": "b", "name": "B renamed", "prompt": "b text"}])
        self.assertEqual(self._names(), ["B renamed"])

    def test_new_default_is_offered_once(self):
        self._names()
        prompt_model.set_prompts_for_language("en", [])
        prompt_model.DEFAULT_PROMPTS["en"].append({"id": "c", "name": "C", "prompt": "c text"})  # app update
        self.assertEqual(self._names(), ["C"])
        prompt_model.set_prompts_for_language("en", [])
        self.assertEqual(self._names(), [])

    def test_new_default_with_a_custom_presets_name_is_skipped(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"en": [{"name": "B", "prompt": "my own"}]}, f)
        self.assertEqual(self._names(), ["B"])
        self.assertEqual(prompt_model.get_prompts_for_language("en")[0]["prompt"], "my own")

    def test_settings_reset_does_not_rerun_the_merge(self):
        self._names()
        prompt_model.set_prompts_for_language("en", [{"name": "Mine", "prompt": "x"}])
        data_reset.reset_settings(keep_api_keys=True)
        self.assertEqual(self._names(), ["Mine"])

    def test_reset_presets_restores_every_default(self):
        self._names()
        prompt_model.set_prompts_for_language("en", [{"name": "Mine", "prompt": "x"}])
        data_reset.reset_prompts()
        self.assertEqual(self._names(), ["A", "B"])

    def test_turkish_new_install_and_existing_list(self):
        with mock.patch.object(prompt_model, "DEFAULT_PROMPTS", real_defaults), \
                mock.patch.object(prompt_model, "LEGACY_DEFAULT_IDS", real_legacy):
            prompt_model.load_prompts()  # new install
            tr = prompt_model.get_prompts_for_language("tr")
            self.assertEqual(tr, real_defaults["tr"])
            self.assertEqual([p["name"] for p in tr][:2], ["Fragman", "Aksiyon Film"])
            # Existing tr user from a version without ids, one default deleted: nothing added back.
            mine = [{"name": "Fragman", "prompt": "x"}, {"name": "Benim", "prompt": "y"}]
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"en": [], "tr": mine}, f)
            prompt_model.load_prompts()
            self.assertEqual(prompt_model.get_prompts_for_language("tr"), mine)

    def test_broken_file_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self._names(), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
