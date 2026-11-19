import sys
import types
import unittest
from unittest import mock

try:
    import wx  # noqa: F401
except ImportError:  # i18n_setup imports wx; the retry logic does not need it
    sys.modules.setdefault("audio_describer.i18n_setup", types.SimpleNamespace(_=lambda s: s))

from audio_describer.core import gemini_helpers as g


class _Client:
    def __init__(self, errors):
        self.errors, self.calls, self.used = list(errors), 0, []
        self.models = self

    def generate_content(self, model=None, **kwargs):
        self.calls += 1
        self.used.append(model)
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


OVERLOAD = Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")


class RetryTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(g.app_state, "sleep_or_cancel")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        mock.patch.object(g, "_lazy_import_gemini_sdk", lambda: None).start()
        self.addCleanup(mock.patch.stopall)

    def test_overload_is_announced_as_overload_and_waits_longer(self):
        messages = []
        client = _Client([OVERLOAD] * 3)
        self.assertEqual(g.generate_content_with_retry(client, "m", [], None, messages.append), "ok")
        self.assertEqual(client.used, ["m"] * 4)
        self.assertTrue(all("overloaded" in m for m in messages), messages)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [15, 30, 60])

    def test_switches_to_a_fallback_model_when_still_overloaded(self):
        messages = []
        client = _Client([OVERLOAD] * g.OVERLOAD_MAX_RETRIES)
        with mock.patch.object(g.config_module, "FALLBACK_GEMINI_MODELS", ["m", "fb1", "fb2"]):
            self.assertEqual(g.generate_content_with_retry(client, "m", [], None, messages.append), "ok")
        self.assertEqual(client.used, ["m"] * g.OVERLOAD_MAX_RETRIES + ["fb1"])
        self.assertTrue(any("fb1" in m for m in messages), messages)

    def test_overload_gives_up_with_a_clear_message(self):
        client = _Client([OVERLOAD] * 20)
        with mock.patch.object(g.config_module, "FALLBACK_GEMINI_MODELS", ["fb1"]),                 self.assertRaises(g.GeminiAPIError) as ctx:
            g.generate_content_with_retry(client, "gemini-x", [], None)
        self.assertEqual(client.calls, g.OVERLOAD_MAX_RETRIES + g.FALLBACK_MAX_RETRIES)
        self.assertIn("gemini-x", str(ctx.exception))

    def test_rate_limit_and_other_errors(self):
        messages = []
        client = _Client([Exception("429 RESOURCE_EXHAUSTED")])
        g.generate_content_with_retry(client, "m", [], None, messages.append)
        self.assertIn("Rate limit", messages[0])
        with self.assertRaises(ValueError):
            g.generate_content_with_retry(_Client([ValueError("bad request")]), "m", [], None)


if __name__ == "__main__":
    unittest.main()
