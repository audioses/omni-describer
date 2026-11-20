"""Old flat (PyInstaller 5) layout leftovers after updating to the _internal/ layout."""
import logging
import os
import tempfile
import unittest

from audio_describer.utils.layout_cleanup import remove_stale_flat_layout, stale_flat_layout_entries

# What a flat release left in the app folder, plus the new build and user data.
OLD_FLAT = ["omni_describer.exe", "updater.exe", "sapi32.exe", "python311.dll", "VCRUNTIME140.dll",
            "_ssl.pyd", "base_library.zip", "bin", "locale", "notifs", "doc", "wx",
            "libcrypto-1_1.dll"]  # a DLL the new build no longer ships
USER_DATA = ["app_log.txt", "updater_log.txt", "early_crash_2026-01-01.txt", "temp_processing",
             "debug_ai_outputs", "settings.dat2", "prompts.json", "voices.json", "updater.exe.new",
             "my_film_descriptions.srt", "My Exports"]
NEW_INTERNAL = ["python311.dll", "vcruntime140.dll", "_ssl.pyd", "base_library.zip", "bin", "locale",
                "notifs", "doc", "wx", "libcrypto-3.dll"]
STALE = ["VCRUNTIME140.dll", "_ssl.pyd", "base_library.zip", "bin", "doc", "libcrypto-1_1.dll",
         "locale", "notifs", "python311.dll", "wx"]


class StaleEntriesTest(unittest.TestCase):
    def test_flat_to_internal_removes_only_old_layout(self):
        top = OLD_FLAT + USER_DATA + ["_internal"]
        self.assertEqual(stale_flat_layout_entries(top, NEW_INTERNAL), STALE)

    def test_already_new_layout_is_noop(self):
        top = ["omni_describer.exe", "updater.exe", "sapi32.exe", "_internal"] + USER_DATA
        self.assertEqual(stale_flat_layout_entries(top, NEW_INTERNAL), [])

    def test_no_internal_dir_is_noop(self):
        self.assertEqual(stale_flat_layout_entries(OLD_FLAT + USER_DATA, []), [])

    def test_keep_list_wins_over_internal_names(self):
        kept = ["app_log.txt", "temp_processing", "settings.dat2", "prompts.json", "updater.exe.new"]
        self.assertEqual(stale_flat_layout_entries(kept, kept), [])


class RemoveTest(unittest.TestCase):
    def test_removes_on_disk_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as app:
            for name in ["omni_describer.exe", "python311.dll", "app_log.txt"]:
                open(os.path.join(app, name), "w").close()
            os.makedirs(os.path.join(app, "bin", "vlc", "plugins"))
            open(os.path.join(app, "bin", "vlc", "libvlc.dll"), "w").close()
            os.makedirs(os.path.join(app, "_internal", "bin"))
            open(os.path.join(app, "_internal", "python311.dll"), "w").close()
            log = logging.getLogger("test_layout_cleanup")
            for _ in range(2):
                remove_stale_flat_layout(app, log)
                self.assertEqual(sorted(os.listdir(app)), ["_internal", "app_log.txt", "omni_describer.exe"])
            self.assertEqual(sorted(os.listdir(os.path.join(app, "_internal"))), ["bin", "python311.dll"])

    def test_missing_dir_does_not_raise(self):
        remove_stale_flat_layout(os.path.join(tempfile.gettempdir(), "no-such-app-dir-xyz"),
                                 logging.getLogger("test_layout_cleanup"))


if __name__ == "__main__":
    unittest.main()
