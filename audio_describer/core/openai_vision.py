# audio_describer/core/openai_vision.py
"""Any vision model behind an OpenAI-compatible Chat Completions endpoint.

One client covers cloud providers (OpenAI, OpenRouter, Anthropic's compatibility layer, Mistral,
Groq, xAI, Alibaba Qwen, Together...) and local servers (LM Studio, llama.cpp, vLLM, Jan,
Ollama's /v1). Images go as base64 data URLs, the only form every one of them accepts."""
import json
import urllib.error
import urllib.request

from .. import app_state
from ..i18n_setup import _
from ..models import config_model
from .gemini_helpers import GeminiAPIError

REQUEST_TIMEOUT_SEC = 600


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


def chat(system, prompt, images_b64, json_output=False, temperature=0.2):
    """One request: system + user text + JPEG frames. Returns the reply text."""
    base_url, api_key, model = settings()
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images_b64]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    # ponytail: no response_format; support for json_object/json_schema differs per provider and some
    # reject it, so JSON is asked for in the prompt and extracted from the reply.
    body = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        answer = json.loads(app_state.run_cancellable(_read, request, REQUEST_TIMEOUT_SEC).decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise GeminiAPIError(_("The vision service refused the API key (%(code)d).") % {"code": e.code}) from e
        raise GeminiAPIError(_("The vision service refused the request (model '%(model)s'): %(detail)s")
                             % {"model": model, "detail": detail}) from e
    except TimeoutError as e:
        raise GeminiAPIError(_("The vision service took too long to answer. Try a smaller model or a shorter video.")) from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise GeminiAPIError(_("Could not reach the vision service at %s.") % base_url) from e
    text = _message_text(answer).strip()
    return extract_json(text) if json_output else text

