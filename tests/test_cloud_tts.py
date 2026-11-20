"""Azure / Google TTS request building (urlopen mocked: no network, no key needed)."""
import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from audio_describer.core import cloud_tts

WAV = b"RIFF" + b"\0" * 60

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CloudTTSTest(unittest.TestCase):
    def setUp(self):
        self.out = os.path.join(tempfile.mkdtemp(), "o.wav")

    def test_azure_request(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(WAV)) as urlopen:
            self.assertTrue(cloud_tts.synthesize_azure("Tom & <Ana>", self.out, "K3Y", "westeurope", "fr-FR-HenriNeural"))
        req, kwargs = urlopen.call_args[0][0], urlopen.call_args[1]
        self.assertEqual(req.full_url, "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1")
        self.assertEqual(req.get_header("Ocp-apim-subscription-key"), "K3Y")
        self.assertEqual(req.get_header("X-microsoft-outputformat"), "riff-24khz-16bit-mono-pcm")
        body = req.data.decode()
        self.assertIn("xml:lang=\"fr-FR\"", body)
        self.assertIn("<voice name=\"fr-FR-HenriNeural\">Tom &amp; &lt;Ana&gt;</voice>", body)
        self.assertTrue(kwargs.get("timeout"))
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), WAV)

    def test_azure_rejects_bad_region(self):
        with self.assertRaises(cloud_tts.CloudTTSError):
            cloud_tts.azure_request("x", "k", "evil.com/", "fr-FR-HenriNeural")

    def test_google_request(self):
        answer = json.dumps({"audioContent": base64.b64encode(WAV).decode()}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_Resp(answer)) as urlopen:
            self.assertTrue(cloud_tts.synthesize_google("Bonjour", self.out, "K3Y", "fr-FR-Chirp3-HD-Aoede"))
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, cloud_tts.GOOGLE_URL)
        self.assertNotIn("K3Y", req.full_url)  # key in a header, never in the URL
        self.assertEqual(req.get_header("X-goog-api-key"), "K3Y")
        self.assertEqual(json.loads(req.data), {"input": {"text": "Bonjour"},
                                                "voice": {"languageCode": "fr-FR", "name": "fr-FR-Chirp3-HD-Aoede"},
                                                "audioConfig": {"audioEncoding": "LINEAR16"}})

    def test_http_error_is_clear_and_keyless(self):
        err = urllib.error.HTTPError(cloud_tts.GOOGLE_URL, 403, "Forbidden", {},
                                     io.BytesIO(b'{"error": {"message": "API key not valid."}}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(cloud_tts.CloudTTSError) as cm:
                cloud_tts.synthesize_google("x", self.out, "SECRET", "fr-FR-Chirp3-HD-Aoede")
        self.assertIn("HTTP 403", str(cm.exception))
        self.assertIn("API key not valid", str(cm.exception))
        self.assertNotIn("SECRET", str(cm.exception))

    def test_non_wav_answer_rejected(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b"<html>")):
            with self.assertRaises(cloud_tts.CloudTTSError):
                cloud_tts.synthesize_azure("x", self.out, "k", "westeurope", "fr-FR-HenriNeural")


if __name__ == "__main__":
    unittest.main()
