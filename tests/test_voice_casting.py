"""Per-character voice casting for translated dialogue (pure helpers in core/timing.py)."""
import unittest

from audio_describer.core import timing
from audio_describer.core.timing import Description


def line(speaker=None, gender=None):
    return Description(1, 2, "Ana: Hello.", flags=timing.dialogue_flags(True, gender, speaker))


class VoiceCastingTest(unittest.TestCase):
    def test_resolution_order(self):
        glossary = [{"id": "ana", "voice": "male", "apparent_gender": "female"},
                    {"id": "bob", "voice": "narrator"}, {"id": "cy"}]
        lines = [line("ana", "female"), line("bob", "male"), line("cy", "female"), line(None, "male"), line()]
        cast, changed = timing.apply_voice_casting(lines, glossary)
        self.assertEqual(changed, 2)
        # override -> line's apparent gender -> narrator (None)
        self.assertEqual([timing.voice_gender(d) for d in cast], ["male", None, "female", "male", None])
        self.assertEqual(timing.line_gender(cast[0]), "female")  # the AI's gender is kept
        self.assertEqual(glossary[0]["apparent_gender"], "female")

    def test_removing_override_restores_gender_and_is_idempotent(self):
        cast, _ = timing.apply_voice_casting([line("ana", "female")], [{"id": "ana", "voice": "male"}])
        self.assertEqual(timing.apply_voice_casting(cast, [{"id": "ana", "voice": "male"}])[1], 0)
        back, changed = timing.apply_voice_casting(cast, [{"id": "ana", "voice": None}])
        self.assertEqual((changed, timing.voice_gender(back[0])), (1, "female"))
        self.assertEqual(timing.speaker_of(back[0]), "ana")

    def test_old_glossary_and_bad_values_ignored(self):
        cast, changed = timing.apply_voice_casting([line("ana", "male")], [{"id": "ana", "voice": "robot"}, "junk"])
        self.assertEqual((changed, timing.voice_gender(cast[0])), (0, "male"))

    def test_plain_descriptions_untouched(self):
        plain = (1.0, 2.0, "A car stops.")
        cast, changed = timing.apply_voice_casting([plain], [{"id": "ana", "voice": "male"}])
        self.assertEqual((cast, changed, timing.voice_gender(plain)), ([plain], 0, None))
        self.assertEqual(timing.dialogue_flags(False, "male", "ana"), ())  # only translations carry a voice

    def test_still_a_three_tuple(self):
        start, end, text = line("ana", "female")
        self.assertEqual((start, end, text), (1.0, 2.0, "Ana: Hello."))

    def test_merge_glossary_keeps_user_voice(self):
        glossary = [{"id": "ana", "voice": "male", "apparent_gender": "unknown"}]
        timing.merge_glossary(glossary, [{"id": "ana", "voice": None, "apparent_gender": "female"},
                                         {"id": "bob"}, "junk"])
        self.assertEqual([g["id"] for g in glossary], ["ana", "bob"])
        self.assertEqual((glossary[0]["voice"], glossary[0]["apparent_gender"]), ("male", "female"))


if __name__ == "__main__":
    unittest.main()
