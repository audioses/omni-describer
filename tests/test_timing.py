import unittest

from audio_describer.core import timing


class ParseTimestampTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(timing.parse_timestamp("12.5"), 12.5)
        self.assertEqual(timing.parse_timestamp("01:30"), 90.0)
        self.assertEqual(timing.parse_timestamp("00:10,250"), 10.25)
        # B4: hour-long videos used to have these silently dropped
        self.assertEqual(timing.parse_timestamp("01:02:03.5"), 3723.5)
        self.assertEqual(timing.parse_timestamp(7), 7.0)

    def test_invalid(self):
        for bad in ("", "a:b", "1:2:3:4", "-1", "01:-5", "1::2"):
            with self.assertRaises(ValueError, msg=bad):
                timing.parse_timestamp(bad)


class HelpersTest(unittest.TestCase):
    def test_format_hms(self):
        self.assertEqual(timing.format_hms(754), "12:34")
        self.assertEqual(timing.format_hms(3905), "1:05:05")
        self.assertEqual(timing.format_hms(-3), "00:00")

    def test_db_to_volume(self):
        self.assertEqual(timing.db_to_volume(100, 6), 50)
        self.assertEqual(timing.db_to_volume(100, 12), 25)
        self.assertEqual(timing.db_to_volume(80, 0), 80)

    def test_speech_estimate_uses_160_wpm(self):
        self.assertAlmostEqual(timing.estimate_speech_seconds(" ".join(["w"] * 8)), 3.0)

    def test_remove_consecutive_duplicates(self):
        descs = [(0, 1, "A door opens."), (1, 2, " A door opens. "), (2, 3, "Night."), (3, 4, "A door opens.")]
        cleaned, removed = timing.remove_consecutive_duplicates(descs)
        self.assertEqual(removed, 1)
        self.assertEqual([d[2].strip() for d in cleaned], ["A door opens.", "Night.", "A door opens."])

    def test_crossed_description_index(self):
        descs = [(1.0, 3.0, "a"), (5.10, 5.20, "short"), (8.0, 9.0, "b")]
        self.assertEqual(timing.crossed_description_index(descs, 0.75, 1.0), 0)
        # B12: 100 ms description fully between two 250 ms ticks is still caught
        self.assertEqual(timing.crossed_description_index(descs, 5.0, 5.25), 1)
        self.assertEqual(timing.crossed_description_index(descs, 6.0, 6.25), -1)
        # a big seek is not a crossing
        self.assertEqual(timing.crossed_description_index(descs, 0.0, 7.0), -1)


class P1HelpersTest(unittest.TestCase):
    def test_description_is_a_3_tuple(self):
        d = timing.Description(1, 2.5, "Sam waves.", "2", ["over_budget"])
        start, end, text = d
        self.assertEqual((start, end, text), (1.0, 2.5, "Sam waves."))
        self.assertEqual((d.level, d.flags), (2, ("over_budget",)))
        self.assertEqual(timing.Description(0, 1, "x", 7).level, 1)
        self.assertEqual(timing.level_of((0, 1, "x")), 1)
        import pickle
        self.assertEqual(pickle.loads(pickle.dumps(d)).level, 2)

    def test_parse_silencedetect(self):
        err = ("[silencedetect @ 0x1] silence_start: -0.01\n"
               "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5\n"
               "[silencedetect @ 0x1] silence_start: 10.2\n")
        self.assertEqual(timing.parse_silencedetect(err, 12.0), [(0.0, 2.5), (10.2, 12.0)])
        self.assertEqual(timing.parse_silencedetect(err), [(0.0, 2.5)])

    def test_word_budget_and_flags(self):
        self.assertEqual(timing.word_budget(3.0), 8)
        self.assertEqual(timing.word_budget(0.1), 1)
        long_text = "one two three four five six seven eight nine ten eleven"
        self.assertIn(timing.FLAG_OVER_BUDGET, timing.compute_flags(0, 2, long_text))
        self.assertEqual(timing.compute_flags(0, 3, "Sam opens the door.", [(0, 3)]), ())
        self.assertIn(timing.FLAG_OVERLAPS_SOUND, timing.compute_flags(5, 7, "Sam runs.", [(0, 3)]))
        self.assertEqual(timing.compute_flags(5, 7, "Sam runs.", None), ())  # not analysed
        self.assertIn(timing.FLAG_ON_SCREEN_TEXT, timing.compute_flags(0, 3, "Sign: Exit.", on_screen_text=True))

    def test_extended_pause(self):
        self.assertTrue(timing.needs_extended_pause((0, 1, "a b c d e f g h")))
        self.assertFalse(timing.needs_extended_pause((0, 5, "Sam sits.")))

    def test_webvtt(self):
        vtt = timing.to_webvtt([(0.0, 0.2, "A --> B"), (3661.5, 3663, "Night.")])
        self.assertTrue(vtt.startswith("WEBVTT\n\nNOTE ") and 'kind="descriptions"' in vtt)
        self.assertIn("00:00:00.000 --> 00:00:00.500\nA -> B", vtt)
        self.assertIn("01:01:01.500 --> 01:01:03.000", vtt)

    def test_filter_and_windows(self):
        descs = [timing.Description(0, 1, "a", 1), timing.Description(1, 2, "b", 3), (2, 3, "c")]
        self.assertEqual([d[2] for d in timing.filter_by_level(descs, 1)], ["a", "c"])
        self.assertEqual(len(timing.filter_by_level(descs, 3)), 3)
        self.assertEqual(timing.windows_in_range([(0, 5), (8, 20)], 4, 10), [(0, 1), (4, 6)])

    def test_on_demand(self):
        descs = [timing.Description(1, 2, "Night.", 1), timing.Description(3, 4, "Rain on glass.", 3),
                 timing.Description(30, 31, "Later.", 1)]
        self.assertEqual(timing.on_demand_texts(descs, 5, False), ["Night."])
        self.assertEqual(timing.on_demand_texts(descs, 5, True), ["Night.", "Rain on glass."])
        self.assertEqual(timing.on_demand_texts(descs, 25, False), [])

    def test_rename_and_report(self):
        descs = [timing.Description(1, 3, "Sam waves at Samantha.", 2, ("uncertain",)),
                 timing.Description(10, 11, "Night falls on the city again and again and again.", 1, ("over_budget",))]
        out, n = timing.rename_in_descriptions(descs, "Sam", "Paul")
        self.assertEqual((n, out[0][2]), (1, "Paul waves at Samantha."))
        self.assertEqual((out[0].level, out[0].flags), (2, ("uncertain",)))
        r = timing.quality_report(descs, 30)
        self.assertEqual((r["count"], r["over_budget"], r["uncertain"]), (2, 1, 1))
        self.assertEqual(r["within_budget_pct"], 50.0)
        self.assertEqual(r["longest_gap"], 20)
        self.assertEqual(timing.quality_report([])["count"], 0)

    def test_translation_and_dhash(self):
        windows = [(0.0, 1.0)]
        self.assertIn("overlaps_sound", timing.compute_flags(5, 7, "Hi there.", windows))
        flags = timing.compute_flags(5, 7, "Ken: Hello.", windows, translation=True)
        self.assertEqual(flags, ("translation",))
        self.assertTrue(timing.is_read_text(timing.Description(5, 7, "x", 1, flags)))
        self.assertFalse(timing.is_read_text(timing.Description(5, 7, "x")))
        flat, ramp = [100] * 72, [x * 3 for _ in range(8) for x in range(9)]
        self.assertEqual(timing.dhash(flat), 0)
        self.assertEqual(timing.hash_distance(timing.dhash(flat), timing.dhash(list(reversed(ramp)))), 64)
        self.assertEqual(timing.hash_distance(timing.dhash(ramp), timing.dhash(ramp)), 0)

    def test_text_formats(self):
        d = [timing.Description(1.5, 3, "Sam waves."), timing.Description(3661.25, 3662, "Night.")]
        self.assertEqual(timing.to_srt(d), "1\n00:00:01,500 --> 00:00:03,000\nSam waves.\n\n"
                                           "2\n01:01:01,250 --> 01:01:02,000\nNight.\n\n")
        self.assertEqual(timing.to_text_lines(d), "Sam waves.\nNight.\n")
        self.assertEqual(timing.format_srt_time(-1), "00:00:00,000")

    def test_ducking_expr(self):
        self.assertEqual(timing.merge_intervals([(5, 6), (0, 1), (1.2, 2)], gap=0.5), [(0, 2), (5, 6)])
        self.assertEqual(timing.ducking_volume_expr([], 9), "1")
        expr = timing.ducking_volume_expr([(2, 3)], 6)
        self.assertTrue(expr.startswith("1-0.4988*min(1,"), expr)

        def evaluate(t):  # mirror of the FFmpeg expression
            clip = lambda x, a, b: max(a, min(b, x))
            return 1 - 0.4988 * min(1, clip((t - 1.85) / 0.15, 0, 1) * clip((3.4 - t) / 0.4, 0, 1))
        self.assertAlmostEqual(evaluate(0), 1)
        self.assertAlmostEqual(evaluate(2.5), 0.5012)
        self.assertAlmostEqual(evaluate(4), 1)

    def test_speech_free_windows(self):
        probs = [0.9] * 10 + [0.1] * 50 + [0.8] * 10 + [0.2] * 5
        # frames of 0.1 s: speech 0-1 s, gap 1-6 s, speech 6-7 s, short tail gap 0.5 s (< min_gap)
        self.assertEqual(timing.speech_free_windows(probs, 0.1, min_gap=0.8), [(1.1, 5.9)])
        self.assertEqual(timing.speech_free_windows([0.0] * 30, 0.1, duration=3.0), [(0.0, 3.0)])
        log = "[Parsed_showinfo_2 @ 0x1] n:   0 pts:  6000 pts_time:6       fmt:yuv420p\nother pts_time:9\n"
        self.assertEqual(timing.parse_scene_changes(log), [6.0])

    def test_overlay_text(self):
        out = timing.overlay_text("A sign reads 50% off, $5 only. " * 3, width=30)
        self.assertIn("50%% off", out)
        self.assertIn("$$5", out)
        self.assertTrue(all(len(line) <= 34 for line in out.split("\n")))
        self.assertGreater(out.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()


class NextToReviewTest(unittest.TestCase):
    def test_wraps_and_skips_validated(self):
        D = timing.Description
        descs = [D(0, 1, "a", flags=(timing.FLAG_OVER_BUDGET,)), D(1, 2, "b"),
                 D(2, 3, "c", flags=(timing.FLAG_PAST_TENSE, timing.FLAG_VALIDATED)),
                 D(3, 4, "d", flags=(timing.FLAG_UNCERTAIN,))]
        self.assertEqual(timing.next_to_review(descs, 0), 3)
        self.assertEqual(timing.next_to_review(descs, 3), 0)
        self.assertEqual(timing.next_to_review(descs, 0, -1), 3)
        self.assertEqual(timing.next_to_review(descs, 1, -1), 0)
        self.assertIsNone(timing.next_to_review([D(0, 1, "x")], 0))
        self.assertIsNone(timing.next_to_review([], 0))


class ExtendedExportTest(unittest.TestCase):
    def test_freezes(self):
        D = timing.Description
        descs = [D(1, 3, "a"), D(5, 6, "b"), D(6.5, 8, "c")]
        # a fits (1.5 s in 2 s); b needs 2.5 s in 1 s -> freeze 1.5 s at 6; c needs 2 s, last one, own pause 1.5 s
        freezes = timing.extended_freezes(descs, [1.5, 2.5, 2.0])
        self.assertEqual(freezes, [(6.0, 1.5), (8.0, 0.5)])
        self.assertEqual(timing.shift_for_freezes(5, freezes), 5)
        self.assertEqual(timing.shift_for_freezes(6.5, freezes), 8.0)
        self.assertEqual(timing.shift_for_freezes(9, freezes), 11.0)
        self.assertEqual(timing.freeze_offset_expr(freezes), "1.500*gte(T,6.000)+0.500*gte(T,8.000)")
        self.assertEqual(timing.freeze_offset_expr([]), "0")

    def test_next_description_caps_the_window(self):
        D = timing.Description
        # end time past the next start: the window stops at the next description
        self.assertEqual(timing.extended_freezes([D(0, 10, "a"), D(2, 3, "b")], [3.0, 0.5]), [(2.0, 1.0)])


class ParseSubtitlesTest(unittest.TestCase):
    def test_srt_and_vtt(self):
        srt = "1\r\n00:00:05,000 --> 00:00:07,500\r\nA man <i>runs</i>.\r\nFast.\r\n\r\n2\r\n00:00:01,000 --> 00:00:02,000\r\nDawn.\r\n"
        d = timing.parse_subtitles(srt)
        self.assertEqual([(x[0], x[1], x[2]) for x in d], [(1.0, 2.0, "Dawn."), (5.0, 7.5, "A man runs. Fast.")])
        vtt = timing.to_webvtt(d)
        self.assertEqual([(x[0], x[1], x[2]) for x in timing.parse_subtitles(vtt)], [(1.0, 2.0, "Dawn."), (5.0, 7.5, "A man runs. Fast.")])
        self.assertEqual(timing.parse_subtitles("WEBVTT\n\n01:02.500 --> 01:04.000 align:start\n<v Bob>Hi\n")[0][:3], (62.5, 64.0, "Hi"))
        self.assertEqual(timing.parse_subtitles("garbage"), [])


class PronunciationTest(unittest.TestCase):
    def test_rules(self):
        spec = "Hermione = her-my-oh-nee\nYork=Yorrk\nNew York = Nouillorque\nbad line\n = x"
        self.assertEqual(timing.apply_pronunciations("hermione flies to New York, not York.", spec),
                         "her-my-oh-nee flies to Nouillorque, not Yorrk.")
        self.assertEqual(timing.apply_pronunciations("Yorkshire", spec), "Yorkshire")
        self.assertEqual(timing.apply_pronunciations("a\b", "a = c\1"), "c\1\b")
        self.assertEqual(timing.apply_pronunciations("same", ""), "same")


class VoiceGenderTest(unittest.TestCase):
    def test_gender_only_on_translated_dialogue(self):
        flags = timing.compute_flags(0, 5, "Anna: hello", translation=True, gender="female")
        self.assertIn(timing.FLAG_VOICE_FEMALE, flags)
        self.assertEqual(timing.voice_gender(timing.Description(0, 5, "x", flags=flags)), "female")
        self.assertNotIn(timing.FLAG_VOICE_MALE, timing.compute_flags(0, 5, "A man walks.", gender="male"))
        self.assertIsNone(timing.voice_gender(timing.Description(0, 5, "x",
                          flags=timing.compute_flags(0, 5, "x", translation=True, gender="unknown"))))
        self.assertIsNone(timing.voice_gender((0, 5, "plain tuple")))