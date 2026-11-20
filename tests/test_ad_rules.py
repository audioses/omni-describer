"""AD rules come from prompts/rules/*.json; a translation and the user's ad_rules.json override them key by key."""
import json
import os
import tempfile
import unittest
from unittest import mock

from audio_describer.core import prompts


class AdRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rules_dir = os.path.join(self.tmp.name, "prompts", "rules")
        self.data_dir = os.path.join(self.tmp.name, "data")
        os.makedirs(self.rules_dir)
        os.makedirs(self.data_dir)
        self._write(os.path.join(self.rules_dir, "en.json"), {
            "core_directives_title": "RULES:", "core_directives": ["one", "two"],
            "system_video": 'SYS {core_directives} {"json": 1}', "verbosity": {"short": "s", "standard": "std"}})
        self.settings = {"application_language": "fr", "gemini_description_verbosity": "short"}
        for p in (mock.patch.object(prompts.config, "get_prompts_dir", return_value=os.path.dirname(self.rules_dir)),
                  mock.patch.object(prompts.config_model, "APP_DATA_DIR", self.data_dir),
                  mock.patch.object(prompts.config_model, "get_setting", lambda k: self.settings.get(k))):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _write(path, data):
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_translation_then_user_file_override_key_by_key(self):
        self._write(os.path.join(self.rules_dir, "fr.json"), {"core_directives_title": "REGLES:", "verbosity": {"short": "court"}})
        self._write(os.path.join(self.data_dir, prompts.USER_RULES_FILE), {"core_directives": ["mine"]})
        rules = prompts.load_rules()
        self.assertEqual(rules["core_directives_title"], "REGLES:")
        self.assertEqual(rules["core_directives"], ["mine"])
        self.assertEqual(rules["verbosity"], {"short": "court", "standard": "std"})

    def test_missing_translation_wrong_type_or_broken_file_keeps_the_default(self):
        self._write(os.path.join(self.data_dir, prompts.USER_RULES_FILE), {"core_directives": "not a list"})
        self.assertEqual(prompts.load_rules()["core_directives"], ["one", "two"])
        self._write(os.path.join(self.data_dir, prompts.USER_RULES_FILE), "{broken")
        self.assertEqual(prompts.load_rules()["core_directives"], ["one", "two"])

    def test_system_prompt_numbers_directives_and_keeps_json_braces(self):
        system, _ = prompts.build_unified_prompts("")
        self.assertEqual(system, 'SYS \nRULES:\n1.  one\n2.  two\n {"json": 1}')

    def test_shipped_english_rules_have_every_key_the_code_uses(self):
        path = os.path.join(os.path.dirname(prompts.__file__), "..", "prompts", "rules", "en.json")
        with open(path, encoding="utf-8") as f:
            rules = json.load(f)
        for key in ("system_video", "core_directives", "task", "task_with_glossary", "task_header",
                    "language_line", "verbosity_line", "verbosity", "user_focus"):
            self.assertIn(key, rules)
        self.assertIn("{core_directives}", rules["system_video"])
        self.assertEqual(set(rules["verbosity"]), {"short", "standard", "detailed"})


if __name__ == "__main__":
    unittest.main()
