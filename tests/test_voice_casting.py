"""Per-character voice casting (timing) and Azure / Google TTS request building (urlopen mocked)."""
import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from audio_describer.core import cloud_tts, timing
from audio_describer.core.timing import Description

WAV = b"RIFF" + b"\0" * 60


def line(speaker=None, gender=None):
    flags = [timing.FLAG_TRANSLATION]
    if gender:
        flags.append(timing.GENDER_FLAGS[gender])
    if speaker:
        flags.append(timing.SPEAKER_PREFIX + speaker)
    return Description(1, 2, "Ana: Hello.", flags=flags)


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
        self.assertEqual(timing.speaker_flags(back[0]), ("speaker:ana",))

    def test_old_glossary_and_bad_values_ignored(self):
        cast, changed = timing.apply_voice_casting([line("ana", "male")], [{"id": "ana", "voice": "robot"}, "junk"])
        self.assertEqual((changed, timing.voice_gender(cast[0])), (0, "male"))


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
