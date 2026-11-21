# audio_describer/core/gemini_helpers.py
import sys
import importlib.util
import json
import os
import re
import datetime
import threading
import time
from typing import Any, Union

from ..i18n_setup import _
from .. import app_state, config
from ..utils.logger import app_logger
from ..models import config_model

# --- Retry Configuration ---
MAX_RETRIES = 3
INITIAL_RETRY_DELAY_SEC = 5
MAX_RETRY_DELAY_SEC = 60

# --- Enhanced Debugging for PyInstaller and google-generativeai ---
app_logger.debug(f"Python sys.path for SDK import: {sys.path}")
google_spec = importlib.util.find_spec("google")
if google_spec:
    app_logger.debug(f"Found 'google' namespace package. Search locations: {google_spec.submodule_search_locations}")
else:
    app_logger.warning("Could not find spec for the 'google' namespace package.")

try:
    genai_spec = importlib.util.find_spec("google.generativeai")
except ModuleNotFoundError:  # no 'google' package at all (e.g. CI, which skips the SDK)
    genai_spec = None
if genai_spec:
    app_logger.debug(f"Found 'google.generativeai' spec before import attempt. Origin: {genai_spec.origin}")
else:
    app_logger.warning("Could not find spec for 'google.generativeai' before import attempt. This is a likely indicator of a packaging issue.")
# --- End Enhanced Debugging ---


# --- Lazy-loading Gemini SDK ---
genai = None
types = None
HarmCategory = None
HarmBlockThreshold = None
google_api_exceptions = None
GEMINI_SDK_AVAILABLE = False
_sdk_import_lock = threading.Lock()

def _lazy_import_gemini_sdk():
    """
    Imports the Gemini SDK modules when first needed.
    This defers potential startup crashes from native libraries until they are actually used.
    """
    global genai, types, HarmCategory, HarmBlockThreshold, google_api_exceptions, GEMINI_SDK_AVAILABLE
    
    if GEMINI_SDK_AVAILABLE:
        return

    with _sdk_import_lock:
        if GEMINI_SDK_AVAILABLE:
            return

        app_logger.info("Attempting to lazy-load Gemini SDK...")
        try:
            from google import genai as genai_module
            from google.genai import types as types_module
            from google.generativeai.types import HarmCategory as HarmCategory_module, HarmBlockThreshold as HarmBlockThreshold_module
            import google.api_core.exceptions as google_api_exceptions_module

            genai = genai_module
            types = types_module
            HarmCategory = HarmCategory_module
            HarmBlockThreshold = HarmBlockThreshold_module
            google_api_exceptions = google_api_exceptions_module
            
            GEMINI_SDK_AVAILABLE = True
            app_logger.info(f"Successfully lazy-imported Gemini SDK. Location: {genai.__file__}")
        
        except ImportError as e:
            GEMINI_SDK_AVAILABLE = False
            app_logger.error("Failed to lazy-import Gemini SDK: %s", e, exc_info=True)
            raise GeminiAPIError("Google's Gemini SDK could not be loaded. This might be due to a compatibility issue or a corrupted installation.") from e
        except Exception as e:
            GEMINI_SDK_AVAILABLE = False
            app_logger.critical("A critical, non-import error occurred during Gemini SDK lazy-loading: %s", e, exc_info=True)
            raise GeminiAPIError("A critical error occurred while loading Google's Gemini SDK, which may be due to a system incompatibility.") from e


class GeminiAPIError(Exception):
    """Custom exception for Gemini API errors."""
    pass

class ContentBlockedError(GeminiAPIError):
    """Exception raised when content is blocked by safety filters or other reasons."""
    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason

class TokenLimitError(GeminiAPIError):
    """Exception raised when the AI process stops because it hit a token limit."""
    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason

# --- Global Client Instance ---
_GEMINI_CLIENT = None

def reset_gemini_client():
    """Resets the global Gemini client instance, forcing re-initialization on next use."""
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is not None:
        app_logger.info("Resetting Gemini API client due to settings change.")
        _GEMINI_CLIENT = None

def get_gemini_client():
    """Gets or initializes the global Gemini client."""
    global _GEMINI_CLIENT
    _lazy_import_gemini_sdk()

    if _GEMINI_CLIENT:
        return _GEMINI_CLIENT

    api_key = config_model.get_setting("user_gemini_api_key") or config.GEMINI_API_KEY
    if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
        raise GeminiAPIError(_("Gemini API Key is not configured in settings."))
    try:
        app_logger.info("Initializing genai.Client...")
        _GEMINI_CLIENT = genai.Client(api_key=api_key)
        return _GEMINI_CLIENT
    except Exception as e:
        _GEMINI_CLIENT = None
        app_logger.error("Failed to initialize Gemini Client: %s", e, exc_info=True)
        raise GeminiAPIError(_("Failed to initialize Gemini Client: %s") % e)

def build_safety_settings():
    """Constructs the safety_settings list for the Gemini API call."""
    _lazy_import_gemini_sdk()
    disable_safety = config_model.get_setting("gemini_disable_safety_block_none")

    if disable_safety:
        app_logger.warning("Disabling all Gemini safety filters as per user setting.")
        return [
            {'category': HarmCategory.HARM_CATEGORY_HARASSMENT.name, 'threshold': HarmBlockThreshold.BLOCK_NONE.name},
            {'category': HarmCategory.HARM_CATEGORY_HATE_SPEECH.name, 'threshold': HarmBlockThreshold.BLOCK_NONE.name},
            {'category': HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT.name, 'threshold': HarmBlockThreshold.BLOCK_NONE.name},
            {'category': HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT.name, 'threshold': HarmBlockThreshold.BLOCK_NONE.name},
        ]
    else:
        return None

def build_generation_config(system_instruction_text=None, is_json_response=False, enable_thinking=False):
    """Builds the generation config for the API call."""
    _lazy_import_gemini_sdk()
    
    config_params = {}

    if (temp_str := config_model.get_setting("gemini_temperature")) is not None:
        try:
            config_params["temperature"] = float(temp_str)
        except ValueError:
            app_logger.warning(f"Invalid temperature value '{temp_str}' in settings, using default 0.2.")
            config_params["temperature"] = 0.2
    else:
        config_params["temperature"] = 0.2

    if enable_thinking:
        model_name_to_use = config_model.get_setting("gemini_model_override") or config.GEMINI_MODEL_NAME
        if "1.5" in model_name_to_use or "2.5" in model_name_to_use:
            app_logger.info(f"Model '{model_name_to_use}' supports thinking. Enabling thinking_config.")
            thinking_config = types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=-1
            )
            config_params["thinking_config"] = thinking_config
        else:
            app_logger.info(f"Model '{model_name_to_use}' may not support thinking. Disabling thinking_config.")

    if is_json_response:
        config_params["response_mime_type"] = "application/json"
    
    safety_settings = build_safety_settings()
    if safety_settings:
        config_params["safety_settings"] = safety_settings

    if system_instruction_text and isinstance(system_instruction_text, str) and system_instruction_text.strip():
        config_params["system_instruction"] = types.Content(parts=[types.Part.from_text(text=system_instruction_text)])

    app_logger.info(f"Built GenerationConfig. JSON: {is_json_response}, Thinking: {enable_thinking}. Params: {config_params}")
    return types.GenerateContentConfig(**config_params)

def log_token_usage(context, response):
    """Logs token usage from a response."""
    usage = getattr(response, 'usage_metadata', None)
    usage_dict = {}
    if usage:
        prompt_tokens = getattr(usage, 'prompt_token_count', 0)
        candidates_tokens = getattr(usage, 'candidates_token_count', None)
        total_tokens = getattr(usage, 'total_token_count', 0)
        thoughts_tokens = getattr(usage, 'thoughts_token_count', 0)

        usage_dict = {
            'prompt_tokens': prompt_tokens,
            'candidates_tokens': candidates_tokens,
            'total_tokens': total_tokens,
            'thoughts_tokens': thoughts_tokens
        }
        app_logger.info(f"Tokens ({context}): Prmpt={prompt_tokens}, Thnk={thoughts_tokens}, Ans={candidates_tokens}, Tot={total_tokens}")
    else:
        app_logger.warning(f"Token usage metadata not found in response for {context}.")
    return usage_dict

def generate_content_with_retry(client, model, contents, config, status_callback=None):
    """Calls client.models.generate_content with exponential backoff on rate-limit errors.

    Retries on HTTP 429 (ResourceExhausted / rate limit) and 503 (service unavailable).
    All other errors are raised immediately.
    """
    _lazy_import_gemini_sdk()

    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        app_state.raise_if_cancelled()  # never start another paid request after Escape
        try:
            response = app_state.run_cancellable(
                client.models.generate_content, model=model, contents=contents, config=config
            )
            return response
        except app_state.GenerationCancelled:
            raise
        except Exception as e:
            error_str = str(e).lower()
            is_retryable = False

            # Check for google.api_core rate-limit / quota / unavailable errors
            if google_api_exceptions is not None:
                if isinstance(e, (google_api_exceptions.ResourceExhausted,
                                  google_api_exceptions.ServiceUnavailable,
                                  google_api_exceptions.TooManyRequests)):
                    is_retryable = True

            # Fallback: check error message for common rate-limit indicators
            if not is_retryable:
                for keyword in ("429", "resource exhausted", "rate limit", "quota",
                                "503", "service unavailable", "overloaded"):
                    if keyword in error_str:
                        is_retryable = True
                        break

            if not is_retryable or attempt == MAX_RETRIES:
                raise

            last_exception = e
            delay = min(INITIAL_RETRY_DELAY_SEC * (2 ** (attempt - 1)), MAX_RETRY_DELAY_SEC)
            retry_msg = _("Rate limit hit (attempt %(attempt)d/%(max)d). Retrying in %(delay)d seconds...") % {
                "attempt": attempt, "max": MAX_RETRIES, "delay": delay
            }
            app_logger.warning(f"Rate limit / transient error on attempt {attempt}: {e}. Retrying in {delay}s...")
            if status_callback:
                status_callback(retry_msg)
            app_state.sleep_or_cancel(delay)

    # Should not reach here, but just in case
    raise last_exception or GeminiAPIError(_("Failed after %d retries.") % MAX_RETRIES)


def process_gemini_response(response, status_callback):
    """Processes a Gemini response, handling thoughts and errors."""
    final_text_parts = []
    thoughts_found = False
    
    if hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason:
        reason = response.prompt_feedback.block_reason.name
        block_msg = _("AI request was blocked due to: %s") % reason
        if status_callback: status_callback(block_msg)
        app_logger.warning(block_msg)
        raise ContentBlockedError(block_msg, reason=reason)

    if not hasattr(response, 'candidates') or not response.candidates:
        if status_callback: status_callback(_("AI returned no answer or an invalid response structure."))
        app_logger.warning("Response has no candidates list or it's empty.")
        return "", False

    candidate = response.candidates[0]

    if not hasattr(candidate, 'content') or not candidate.content or not candidate.content.parts:
        finish_reason_obj = getattr(candidate, 'finish_reason', None)
        finish_reason_name = getattr(finish_reason_obj, 'name', 'UNKNOWN') if finish_reason_obj else 'NOT_SPECIFIED'
        
        if finish_reason_name == 'MAX_TOKENS':
            block_msg = _("AI process stopped because it reached its processing limit (MAX_TOKENS).")
            if status_callback: status_callback(block_msg)
            app_logger.warning(f"Response candidate finished with reason 'MAX_TOKENS' and had no content parts.")
            raise TokenLimitError(block_msg, reason=finish_reason_name)
            
        if finish_reason_name != 'STOP':
            block_msg = _("AI content generation was stopped. Reason: %s") % finish_reason_name
            if status_callback: status_callback(block_msg)
            app_logger.warning(f"Response candidate finished with reason '{finish_reason_name}' and had no content parts.")
            raise ContentBlockedError(block_msg, reason=finish_reason_name)
        else:
            if status_callback: status_callback(_("AI returned an empty response."))
            app_logger.warning("Response candidate finished with STOP but had no content parts.")
            return "", False

    for part in candidate.content.parts:
        text_content = getattr(part, 'text', "") or ""
        if getattr(part, "thought", None): 
            thoughts_found = True
            if status_callback: status_callback(_("AI is thinking..."))
            app_logger.info(f"🧠 AI Thought: {text_content.strip()}")
        else:
            final_text_parts.append(text_content)
    
    if thoughts_found:
        app_logger.info("AI completed thinking process.")

    final_text_result = "".join(final_text_parts).strip()
    
    if hasattr(candidate, 'finish_reason') and candidate.finish_reason.name == 'SAFETY':
        reason = candidate.finish_reason.name
        block_msg = _("AI content generation was stopped due to: %s") % reason
        if status_callback: status_callback(block_msg)
        app_logger.warning(block_msg)
        raise ContentBlockedError(block_msg, reason=reason)

    if not final_text_result and not thoughts_found:
         app_logger.warning("Processed response, but final text and thoughts are empty.")
         return "", False
         
    return final_text_result, True

def _serialize_gemini_response_to_dict(obj):
    """
    Recursively converts a Gemini SDK object into a dictionary suitable for JSON serialization.
    """
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    
    if isinstance(obj, (list, tuple)):
        return [_serialize_gemini_response_to_dict(item) for item in obj]

    if hasattr(obj, 'pb') and hasattr(obj.pb, 'DESCRIPTOR'):
        try:
            from google.protobuf.json_format import MessageToDict
            return MessageToDict(obj.pb, preserving_proto_field_name=True, use_integers_for_enums=True)
        except ImportError:
            app_logger.warning("google.protobuf.json_format.MessageToDict not found. Falling back to manual serialization for Protobuf objects.")
            d = {}
            for field in obj.pb.DESCRIPTOR.fields:
                field_name = field.name
                if hasattr(obj.pb, field_name):
                    value = getattr(obj.pb, field_name)
                    d[field_name] = _serialize_gemini_response_to_dict(value)
            return d
    
    if hasattr(obj, '__dict__'):
        d = {}
        for k, v in obj.__dict__.items():
            if k.startswith('_'):
                continue
            d[k] = _serialize_gemini_response_to_dict(v)
        return d
    
    if hasattr(obj, 'name') and isinstance(obj.name, str):
        return obj.name

    return str(obj)

def save_raw_ai_output(video_filename: str, output_type: str, content: Union[str, Any], suffix: str = ""):
    """
    Saves the raw AI response (either string or object) to a file in a debug folder if running in non-frozen mode.
    """
    if getattr(sys, 'frozen', False):
        return

    try:
        _lazy_import_gemini_sdk()

        sanitized_video_name = re.sub(r'[\\/:*?"<>|]', '_', os.path.splitext(video_filename)[0])[:50]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        file_extension = ".txt"
        data_to_write_str = ""

        if isinstance(content, str):
            data_to_write_str = content
            file_extension = ".txt"
        elif GEMINI_SDK_AVAILABLE and isinstance(content, genai.types.GenerateContentResponse):
            data_dict = _serialize_gemini_response_to_dict(content)
            data_to_write_str = json.dumps(data_dict, indent=4, ensure_ascii=False)
            file_extension = ".json"
        else:
            try:
                data_to_write_str = json.dumps(content, indent=4, ensure_ascii=False)
                file_extension = ".json"
            except TypeError:
                app_logger.error(f"Cannot serialize object of type {type(content)} to JSON directly. Saving as plain text (repr).", exc_info=True)
                data_to_write_str = repr(content)
                file_extension = ".txt"

        output_filename = f"{sanitized_video_name}_{output_type}{suffix}_{timestamp}{file_extension}"
        output_dir = os.path.abspath(os.path.join(config.get_app_root(), "..", "debug_ai_outputs")) 
        os.makedirs(output_dir, exist_ok=True)
        
        file_path = os.path.join(output_dir, output_filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(data_to_write_str)
        
        app_logger.info(f"Raw AI output saved to: {file_path}")
    except Exception as e:
        app_logger.error(f"Failed to save raw AI output to file: {e}", exc_info=True)