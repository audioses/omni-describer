# audio_describer/core/cloud_tts.py
"""Cloud neural voices over plain REST (urllib, no SDK): Azure Neural TTS and Google Cloud
Text-to-Speech. Both return a 16-bit mono WAV. Keys travel in headers only, never in a URL or a log."""
import base64
import json
import re
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr

TIMEOUT_SEC = 30
MAX_AUDIO_BYTES = 50 * 1024 * 1024
GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"


class CloudTTSError(Exception):
    pass


def _locale(voice):
    """'fr-FR-DeniseNeural' / 'fr-FR-Chirp3-HD-Aoede' -> 'fr-FR'."""
    return "-".join(voice.split("-")[:2])


def azure_request(text, key, region, voice):
    region = (region or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]+", region):  # the region is part of the host name
        raise CloudTTSError("Azure region is missing or invalid (for example: westeurope).")
    if not key:
        raise CloudTTSError("Azure Speech key is not configured.")
    if not voice:
        raise CloudTTSError("No Azure voice name is configured (for example: fr-FR-DeniseNeural).")
    ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang={quoteattr(_locale(voice))}>'
            f'<voice name={quoteattr(voice)}>{escape(text)}</voice></speak>')
    return urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml.encode("utf-8"), method="POST",
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm", "User-Agent": "OmniDescriber"})


def google_request(text, key, voice):
    if not key:
        raise CloudTTSError("Google Cloud API key is not configured.")
    if not voice:
        raise CloudTTSError("No Google voice name is configured (for example: fr-FR-Chirp3-HD-Aoede).")
    body = {"input": {"text": text}, "voice": {"languageCode": _locale(voice), "name": voice},
            "audioConfig": {"audioEncoding": "LINEAR16"}}
    return urllib.request.Request(GOOGLE_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                  headers={"X-Goog-Api-Key": key, "Content-Type": "application/json"})


def _send(request, provider):
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            return response.read(MAX_AUDIO_BYTES + 1)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read(4096) or b"{}").get("error", {}).get("message", "")
        except (ValueError, AttributeError, OSError):
            pass
        hint = " (check the key and region)" if e.code in (401, 403) else ""
        raise CloudTTSError(f"{provider} refused the request: HTTP {e.code}{hint}. {detail}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CloudTTSError(f"{provider} could not be reached: {getattr(e, 'reason', e)}") from None


def _write_wav(audio, output_wav_path, provider):
    if len(audio) > MAX_AUDIO_BYTES or audio[:4] != b"RIFF" or len(audio) <= 44:
        raise CloudTTSError(f"{provider} returned no usable audio.")
    with open(output_wav_path, "wb") as f:
        f.write(audio)
    return True


def synthesize_azure(text, output_wav_path, key, region, voice):
    return _write_wav(_send(azure_request(text, key, region, voice), "Azure"), output_wav_path, "Azure")


def synthesize_google(text, output_wav_path, key, voice):
    raw = _send(google_request(text, key, voice), "Google")
    try:
        audio = base64.b64decode(json.loads(raw)["audioContent"])  # LINEAR16 comes with a WAV header
    except (ValueError, KeyError, TypeError):
        raise CloudTTSError("Google returned an unexpected answer.") from None
    return _write_wav(audio, output_wav_path, "Google")
