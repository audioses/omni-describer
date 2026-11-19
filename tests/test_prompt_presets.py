import unittest

from audio_describer.models import prompt_model


class DefaultPresetsTest(unittest.TestCase):
    def test_every_language_matches_english(self):
        en = len(prompt_model.DEFAULT_PROMPTS["en"])
        for lang, presets in prompt_model.DEFAULT_PROMPTS.items():
            self.assertEqual(len(presets), en, lang)
            names = [p["name"] for p in presets]
            self.assertEqual(len(names), len(set(names)), lang)  # merge is by name
            prompt_model.validate_presets(presets)

    def test_unedited_old_default_is_upgraded_edited_kept(self):
        old = ("This is a tutorial or DIY video. Focus on the step-by-step process. Clearly describe the specific "
               "tools and materials used. Detail the action being performed on the object, and describe the state "
               "of the object before and after the action (e.g., 'He drills a hole through the marked spot on the plank.').")
        presets = [{"name": "DIY / Tutorial", "prompt": old}, {"name": "Kids", "prompt": "my own text"}]
        self.assertEqual(prompt_model._upgrade_unedited_presets(presets, prompt_model.DEFAULT_PROMPTS["en"]), 1)
        self.assertIn("The carpenter drills", presets[0]["prompt"])
        self.assertEqual(presets[1]["prompt"], "my own text")

    def test_hint_text_glued_by_old_dialog_is_removed(self):
        presets = [{"name": "adi2A short, descriptive name for the prompt preset.",
                    "prompt": "The full text of the prompt to be sent to the AI.Describe calmly."}]
        self.assertEqual(prompt_model._repair_hint_text(presets), 2)
        self.assertEqual(presets, [{"name": "adi2", "prompt": "Describe calmly."}])


if __name__ == "__main__":
    unittest.main()
