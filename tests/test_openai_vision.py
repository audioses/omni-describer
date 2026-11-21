"""OpenAI-compatible vision service: request shape, reply parsing, settings checks (network mocked)."""
import importlib.util
import io
import json
import sys
import urllib.error
import types
import unittest
from unittest import mock

if importlib.util.find_spec("wx") is None:  # i18n_setup needs wx; gettext passthrough is enough here
    _i18n = types.ModuleType("audio_describer.i18n_setup")
    _i18n._ = lambda s: s
    sys.modules.setdefault("audio_describer.i18n_setup", _i18n)

try:
    from audio_describer.core import openai_vision
    from audio_describer.core.gemini_helpers import GeminiAPIError
except ImportError:  # google-genai missing
    openai_vision = None


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@unittest.skipIf(openai_vision is None, "app dependencies not installed")
class OpenAIVisionTest(unittest.TestCase):
    def _settings(self, **kw):
        values = {"vision_base_url": "https://openrouter.ai/api/v1/", "vision_model": "qwen/qwen3-vl",
                  "user_vision_api_key": "sk-test", **kw}
        return mock.patch.object(openai_vision.config_model, "get_setting", side_effect=values.get)

    def test_request_shape_and_json_extraction(self):
        sent = []

        def fake_urlopen(request, timeout):
            sent.append(request)
            return _Response({"choices": [{"message": {"content": 'Here:\n```json\n{"audio_descriptions": []}\n```'}}]})

        with self._settings(), mock.patch.object(openai_vision.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = openai_vision.chat("sys", "describe", ["AAAA"], json_output=True)
        self.assertEqual(out, '{"audio_descriptions": []}')
        request = sent[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
        body = json.loads(request.data)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(body["messages"][1]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,AAAA")

    def test_content_as_parts(self):
        reply = {"choices": [{"message": {"content": [{"type": "text", "text": "a cat"}]}}]}
        with self._settings(), mock.patch.object(openai_vision.urllib.request, "urlopen", return_value=_Response(reply)):
            self.assertEqual(openai_vision.chat(None, "q", []), "a cat")

    def test_plain_http_only_for_this_computer(self):
        with self._settings(vision_base_url="http://example.com/v1"):
            self.assertRaises(GeminiAPIError, openai_vision.settings)
        with self._settings(vision_base_url="http://localhost:1234/v1", user_vision_api_key=""):
            self.assertEqual(openai_vision.settings()[0], "http://localhost:1234/v1")
        with self._settings(vision_model=""):
            self.assertRaises(GeminiAPIError, openai_vision.settings)


def _http_error(code, body="", retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://x/chat/completions", code, "err", headers, io.BytesIO(body.encode()))


@unittest.skipIf(openai_vision is None, "app dependencies not installed")
class RetryAndTemperatureTest(unittest.TestCase):
    def setUp(self):
        values = {"vision_base_url": "https://openrouter.ai/api/v1", "vision_model": "openai/o4-mini",
                  "user_vision_api_key": "k"}
        self.patches = [mock.patch.object(openai_vision.config_model, "get_setting", side_effect=values.get),
                        mock.patch.object(openai_vision.app_state, "sleep_or_cancel"),
                        mock.patch.object(openai_vision, "_no_temperature", set())]
        self.sleep = [p.start() for p in self.patches][1]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _run(self, outcomes):
        sent = []

        def fake(request, timeout):
            sent.append(json.loads(request.data))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return _Response(outcome)
        with mock.patch.object(openai_vision.urllib.request, "urlopen", side_effect=fake):
            return openai_vision.chat("s", "p", [], temperature=0.2), sent

    def test_429_then_success_waits_retry_after(self):
        ok = {"choices": [{"message": {"content": "done"}}]}
        text, sent = self._run([_http_error(429, retry_after=7), _http_error(503), ok])
        self.assertEqual(text, "done")
        self.assertEqual(len(sent), 3)
        self.assertEqual(self.sleep.call_args_list[0].args[0], 7.0)

    def test_gives_up_after_max_attempts(self):
        errors = [_http_error(502) for _ in range(openai_vision.MAX_ATTEMPTS)]
        with self.assertRaises(GeminiAPIError):
            self._run(errors)

    def test_error_inside_200_body_is_retried(self):
        ok = {"choices": [{"message": {"content": "x"}}]}
        text, sent = self._run([{"error": {"code": 429, "message": "rate limited upstream"}}, ok])
        self.assertEqual((text, len(sent)), ("x", 2))

    def test_temperature_dropped_when_rejected_and_remembered(self):
        ok = {"choices": [{"message": {"content": "x"}}]}
        rejected = _http_error(400, json.dumps({"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model."}}))
        _text, sent = self._run([rejected, ok])
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])
        _text, sent = self._run([ok])
        self.assertNotIn("temperature", sent[0])
        self.sleep.assert_not_called()

    def test_other_400_is_not_retried(self):
        with self.assertRaises(GeminiAPIError):
            self._run([_http_error(400, "model not found")])

    def test_401_is_not_retried(self):
        with self.assertRaises(GeminiAPIError):
            self._run([_http_error(401)])


if __name__ == "__main__":
    unittest.main()
