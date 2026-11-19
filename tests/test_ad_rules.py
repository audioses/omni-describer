"""AD rules come from prompts/rules/*.json; translations and the user's ad_rules.json override them key by key."""
import json
import os
import tempfile
import unittest
from unittest import mock

from audio_describer.core import prompts


class AdRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rules_dir = os.path.join(self.tmp.name, "prompts", "rules")
        os.makedirs(self.rules_dir)
        self.data_dir = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data_dir)
        self._write(os.path.join(self.rules_dir, "en.json"), {
            "core_directives_title": "RULES:", "core_directives": ["one", "two"],
            "system_video": "SYS {core_directives}", "emphasis": {"text": "read text", "actions": "actions"}})
        self.settings = {"description_language": "fr"}
        for p in (mock.patch.object(prompts.config, "get_prompts_dir", return_value=os.path.dirname(self.rules_dir)),
                  mock.patch.object(prompts.config_model, "APP_DATA_DIR", self.data_dir),
                  mock.patch.object(prompts.config_model, "get_setting", lambda k: self.settings.get(k))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _write(path, data):
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_shipped_rules_have_every_key_the_code_uses(self):
        mock.patch.stopall()  # real files
        with open(os.path.join(os.path.dirname(prompts.__file__), "..", "prompts", "rules", "en.json"), encoding="utf-8") as f:
            rules = json.load(f)
        self.assertEqual(len(rules["core_directives"]), 12)
        for key in ("system_video", "system_frames", "task", "language_line", "pauses", "audio_subtitles", "length"):
            self.assertIn(key, rules)

    def test_translation_then_user_file_override_key_by_key(self):
        self._write(os.path.join(self.rules_dir, "fr.json"), {"core_directives": ["un", "deux"], "emphasis": {"text": "lis"}})
        self._write(os.path.join(self.data_dir, prompts.USER_RULES_FILE), {"core_directives_title": "MES RÈGLES :"})
        rules = prompts.load_rules()
        self.assertEqual(rules["core_directives"], ["un", "deux"])
        self.assertEqual(rules["emphasis"], {"text": "lis", "actions": "actions"})  # dicts merge
        self.assertEqual(prompts.system_prompt("video", rules), "SYS \nMES RÈGLES :\n1.  un\n2.  deux\n")

    def test_wrong_type_or_broken_file_keeps_defaults(self):
        self._write(os.path.join(self.rules_dir, "fr.json"), "{not json")
        self._write(os.path.join(self.data_dir, prompts.USER_RULES_FILE), {"core_directives": "not a list"})
        self.assertEqual(prompts.load_rules()["core_directives"], ["one", "two"])


if __name__ == "__main__":
    unittest.main()
