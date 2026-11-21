# audio_describer/core/openai_vision.py
"""Any vision model behind an OpenAI-compatible Chat Completions endpoint.

One client covers cloud providers (OpenRouter, OpenAI, Mistral, Groq...) and local servers
(LM Studio, llama.cpp, vLLM, Ollama at http://localhost:11434/v1). Images go as base64 data
URLs, the only form every one of them accepts. These models get still frames, not the video
and not its sound."""
import json
import random
import urllib.error
import urllib.request

from .. import app_state
from ..i18n_setup import _
from ..models import config_model
from ..utils.logger import app_logger
from .gemini_helpers import GeminiAPIError

REQUEST_TIMEOUT_SEC = 600
MAX_ATTEMPTS = 6               # first try + 5 retries on 429 / 5xx / network errors
BASE_DELAY_SEC = 2.0
MAX_DELAY_SEC = 60.0
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}

# Models that answered 400 to "temperature" (OpenAI reasoning models, some routers): not sent again.
# ponytail: per-process memory; a restart asks once more, which costs one rejected request.
_no_temperature = set()


class _Retryable(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _read(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def settings():
    """(base_url, api_key, model) from Settings; raises a readable error when incomplete."""
    base_url = (config_model.get_setting("vision_base_url") or "").strip().rstrip("/")
    model = (config_model.get_setting("vision_model") or "").strip()
    api_key = (config_model.get_setting("user_vision_api_key") or "").strip()
    if not base_url.lower().startswith(("https://", "http://")) or not model:
        raise GeminiAPIError(_("Set the server address and the model of the OpenAI-compatible vision service in Settings."))
    if base_url.lower().startswith("http://") and not _is_local(base_url):
        raise GeminiAPIError(_("Use an https:// address for an online vision service; http:// is only for this computer."))
    return base_url, api_key, model


def _is_local(url):
    host = url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _message_text(answer):
    """Text of the first choice, tolerant of providers that return content as parts."""
    try:
        content = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def extract_json(text):
    """The JSON object in a reply, even when wrapped in ```json fences or a sentence."""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text


def _retry_after(headers):
    try:
        return max(0.0, float(headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return None


def _rejects_temperature(status, detail):
    """400/422 whose message is about the temperature parameter."""
    return status in (400, 422) and "temperature" in detail.lower()


def _error_in_body(answer):
    """OpenRouter can answer 200 with {"error": {"code": 429|5xx, ...}} when the upstream provider failed."""
    error = answer.get("error") if isinstance(answer, dict) else None
    if not error:
        return None
    code = error.get("code") if isinstance(error, dict) else None
    message = (error.get("message") if isinstance(error, dict) else str(error)) or ""
    return (code if isinstance(code, int) else 0), message


def _send(base_url, api_key, body):
    """One HTTP call. Returns the parsed answer; raises _Retryable for temporary failures."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers)
    model = body["model"]
    try:
        answer = json.loads(app_state.run_cancellable(_read, request, REQUEST_TIMEOUT_SEC).decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        if e.code in RETRYABLE_STATUS:
            raise _Retryable(f"HTTP {e.code}: {detail[:200]}", _retry_after(e.headers)) from e
        if "temperature" in body and _rejects_temperature(e.code, detail):
            raise _TemperatureRejected() from e
        if e.code in (401, 403):
            raise GeminiAPIError(_("The vision service refused the API key (%(code)d).") % {"code": e.code}) from e
        raise GeminiAPIError(_("The vision service refused the request (model '%(model)s'): %(detail)s")
                             % {"model": model, "detail": detail[:300]}) from e
    except TimeoutError as e:
        raise _Retryable("timeout") from e
    except json.JSONDecodeError as e:
        raise _Retryable("invalid JSON from server") from e
    except (urllib.error.URLError, OSError) as e:
        if _is_local(base_url):  # a local server that isn't running won't start by waiting
            raise GeminiAPIError(_("Could not reach the vision service at %s. Is the local server running?") % base_url) from e
        raise _Retryable(f"network: {e}") from e
    in_body = _error_in_body(answer)
    if in_body:
        code, message = in_body
        if code in RETRYABLE_STATUS:
            raise _Retryable(f"provider error {code}: {message[:200]}")
        if "temperature" in body and _rejects_temperature(code, message):
            raise _TemperatureRejected()
        raise GeminiAPIError(_("The vision service refused the request (model '%(model)s'): %(detail)s")
                             % {"model": model, "detail": message[:300]})
    return answer


class _TemperatureRejected(Exception):
    pass


def _delay(attempt, retry_after):
    """Server's Retry-After when given, else exponential back-off with jitter."""
    if retry_after is not None:
        return min(retry_after, MAX_DELAY_SEC)
    return min(MAX_DELAY_SEC, BASE_DELAY_SEC * 2 ** attempt) * random.uniform(0.75, 1.25)


def chat(system, prompt, images_b64, json_output=False, temperature=0.2, status=None):
    """One request: system + user text + JPEG frames. Returns the reply text.

    Retries 429 / 5xx / timeouts with back-off (Escape stops the wait). A model that rejects
    `temperature` is asked again without it, and never sent it again in this session."""
    base_url, api_key, model = settings()
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images_b64]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    # ponytail: no response_format; support for json_object/json_schema differs per provider and some
    # reject it, so JSON is asked for in the prompt and extracted from the reply.
    body = {"model": model, "messages": messages, "stream": False}
    if temperature is not None and model not in _no_temperature:
        body["temperature"] = temperature
    attempt = 0
    while True:
        app_state.raise_if_cancelled()
        try:
            answer = _send(base_url, api_key, body)
            break
        except _TemperatureRejected:
            app_logger.info(f"Vision model '{model}' rejects temperature; sending without it.")
            _no_temperature.add(model)
            body.pop("temperature", None)
        except _Retryable as e:
            attempt += 1
            if attempt >= MAX_ATTEMPTS:
                raise GeminiAPIError(_("The vision service is still unavailable after %(n)d attempts: %(detail)s")
                                     % {"n": MAX_ATTEMPTS, "detail": str(e)}) from e
            wait = _delay(attempt - 1, e.retry_after)
            app_logger.warning(f"Vision service: {e}; retry {attempt}/{MAX_ATTEMPTS - 1} in {wait:.1f}s")
            if status:
                status(_("The vision service is busy; trying again in %d s...") % round(wait))
            app_state.sleep_or_cancel(wait)
    text = _message_text(answer).strip()
    return extract_json(text) if json_output else text
