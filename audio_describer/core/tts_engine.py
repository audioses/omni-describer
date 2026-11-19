# audio_describer/core/tts_engine.py
import os
import time
import tempfile
import subprocess
import sys
import json
from ..models import config_model, voice_model
from . import timing, cloud_tts
from ..utils.logger import app_logger
from ..i18n_setup import _
from ..utils import sound_player
from ..utils.system_utils import run_command

# --- SAPI5 Imports ---
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False
    app_logger.warning("pyttsx3 library not found. SAPI5 functionality will be disabled.")
    pyttsx3 = None 

# --- OpenAI Imports ---
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    app_logger.warning("openai library not found. OpenAI TTS functionality will be disabled.")
    OpenAI = None

# --- FFmpeg Command Path (for conversion) ---
from .video_processor import FFMPEG_COMMAND, FFMPEG_IS_AVAILABLE

# --- Windows OneCore Imports ---
try:
    import asyncio
    import winsdk.windows.media.speechsynthesis as speechsynthesis
    import winsdk.windows.storage.streams as streams
    WIN_ONECORE_AVAILABLE = True
except ImportError:
    WIN_ONECORE_AVAILABLE = False
    app_logger.warning("winsdk library not found or incomplete. Windows OneCore TTS functionality will be disabled.")
    speechsynthesis = None
    streams = None
    asyncio = None

class TTSError(Exception):
    """Custom exception for TTS errors."""
    pass

# --- MODIFIED: New path logic for 32-bit helper ---
SAPI5_32BIT_HELPER_NAME = "sapi32.exe"

def _get_helper_path():
    """Finds the path to the 32-bit helper executable inside the 'bin' folder."""
    if getattr(sys, 'frozen', False):
        # The release puts sapi32.exe next to omni_describer.exe (like updater.exe);
        # bin/ inside the bundle is still accepted.
        candidates = [os.path.join(os.path.dirname(sys.executable), SAPI5_32BIT_HELPER_NAME),
                      os.path.join(getattr(sys, '_MEIPASS', ''), 'bin', SAPI5_32BIT_HELPER_NAME)]
        return next((p for p in candidates if os.path.exists(p)), candidates[0])
    else:
        # In development, assume it's been built into the main dist folder for testing
        base_dir = os.getcwd()
        return os.path.join(base_dir, "dist", SAPI5_32BIT_HELPER_NAME)

SAPI5_32BIT_HELPER_PATH = _get_helper_path()
SAPI5_32BIT_HELPER_AVAILABLE = os.path.exists(SAPI5_32BIT_HELPER_PATH)

if not SAPI5_32BIT_HELPER_AVAILABLE:
    app_logger.warning(f"32-bit SAPI5 helper not found at '{SAPI5_32BIT_HELPER_PATH}'. 32-bit voice support will be disabled.")
# --- END MODIFICATION ---

def test_voice(engine_choice, text, settings):
    """
    Dispatcher for testing a voice by speaking it directly.
    This function is designed to be run in a background thread.
    `settings` is a dict containing all necessary parameters from the UI.
    """
    app_logger.info(f"Voice test requested for engine: '{engine_choice}'")
    if engine_choice == "openai":
        _test_voice_openai(text, settings)
    elif engine_choice == "sapi5":
        _test_voice_sapi5(text, settings)
    elif engine_choice == "sapi5_32bit":
        # For testing, we generate a temp file and play it, same as synthesis.
        temp_wav_path = os.path.join(tempfile.gettempdir(), f"test_voice_32bit_{os.urandom(4).hex()}.wav")
        try:
            success = _synthesize_with_sapi5_32bit(text, temp_wav_path, override_settings=settings)
            if success:
                sound_player.play(temp_wav_path, is_full_path=True)
        finally:
            if os.path.exists(temp_wav_path):
                time.sleep(1)
                try: os.remove(temp_wav_path)
                except Exception as e: app_logger.error(f"Could not clean up temp 32-bit test file: {e}")
    elif engine_choice == "onecore":
        _test_voice_onecore(text, settings)
    elif engine_choice in CLOUD_ENGINES:
        temp_wav_path = os.path.join(tempfile.gettempdir(), f"test_voice_cloud_{os.urandom(4).hex()}.wav")
        try:
            if _synthesize_with_cloud(engine_choice, text, temp_wav_path, settings=settings):
                sound_player.play(temp_wav_path, is_full_path=True)
        finally:
            if os.path.exists(temp_wav_path):
                time.sleep(1)
                try: os.remove(temp_wav_path)
                except Exception as e: app_logger.error(f"Could not clean up temp cloud test file: {e}")
    else:
        raise TTSError(f"Unknown TTS engine for testing: '{engine_choice}'")

def _test_voice_sapi5(text, settings):
    if not PYTTSX3_AVAILABLE:
        raise TTSError("pyttsx3 library not available for SAPI5.")
    
    voice_id = settings.get("sapi5_voice_id")
    rate_percent = settings.get("sapi5_rate_percent", 100)
    
    engine = None
    try:
        engine = pyttsx3.init(driverName='sapi5')
        if voice_id:
            engine.setProperty('voice', voice_id)
        
        base_rate = 180
        actual_rate = int(base_rate * (rate_percent / 100.0))
        engine.setProperty('rate', actual_rate)
        
        engine.say(text)
        engine.runAndWait()
    finally:
        if engine:
            try:
                engine.stop()
            except Exception as e:
                app_logger.error(f"Error stopping SAPI5 engine after test: {e}")

def _test_voice_onecore(text, settings):
    if not WIN_ONECORE_AVAILABLE:
        raise TTSError("Windows OneCore libraries (winsdk) not available.")

    temp_wav_path = os.path.join(tempfile.gettempdir(), f"test_voice_onecore_{os.urandom(4).hex()}.wav")
    try:
        success = asyncio.run(_synthesize_with_onecore(text, temp_wav_path, override_settings=settings))
        if success:
            sound_player.play(temp_wav_path, is_full_path=True)
    finally:
        if os.path.exists(temp_wav_path):
            time.sleep(1)
            try:
                os.remove(temp_wav_path)
            except Exception as e:
                app_logger.error(f"Could not clean up temp OneCore test file: {e}")

def _test_voice_openai(text, settings):
    if not OPENAI_AVAILABLE:
        raise TTSError("OpenAI library is not installed.")
    
    api_key = settings.get("openai_api_key")
    if not api_key:
        raise TTSError("OpenAI API Key not provided for test.")
        
    preset = voice_model.get_voice_by_name(settings.get("openai_voice_preset"))
    if not preset:
        raise TTSError("Selected voice preset not found.")

    temp_wav_path = os.path.join(tempfile.gettempdir(), f"test_voice_{os.urandom(4).hex()}.wav")
    try:
        success = _synthesize_with_openai(text, temp_wav_path, override_settings=settings)
        if success:
            sound_player.play(temp_wav_path, is_full_path=True)
    finally:
        if os.path.exists(temp_wav_path):
            try:
                time.sleep(1)
                os.remove(temp_wav_path)
            except Exception as e:
                app_logger.error(f"Could not clean up temp test file {temp_wav_path}: {e}")

def _description_language():
    return (config_model.get_setting("description_language")
            or config_model.ui_language() or "en").split("_")[0].lower()


def _engine_rate_percent():
    key = "onecore_voice_rate_percent" if config_model.get_setting("tts_engine") == "onecore" else "sapi5_voice_rate_percent"
    try:
        return int(config_model.get_setting(key) or 100)
    except (TypeError, ValueError):
        return 100


def _onecore_voice_for(gender, lang):
    """An installed Windows voice of that gender and language, other than the description voice."""
    if not WIN_ONECORE_AVAILABLE:
        return None
    wanted = speechsynthesis.VoiceGender.FEMALE if gender == "female" else speechsynthesis.VoiceGender.MALE
    main = config_model.get_setting("onecore_voice_id") if config_model.get_setting("tts_engine") == "onecore" else None
    try:
        voices = list(speechsynthesis.SpeechSynthesizer.all_voices)
    except Exception as e:
        app_logger.warning(f"OneCore voices unavailable: {e}")
        return None
    return next((v.id for v in voices if v.gender == wanted and v.id != main
                 and v.language.lower().startswith(lang)), None)


def synthesize_with_gender(text, output_wav_path, gender):
    """Translated dialogue read with a voice matching the speaker ('female' / 'male').
    True if such a voice spoke; False when none is available (the caller uses the usual voice)."""
    if gender not in ("female", "male") or not config_model.get_setting("dialogue_voice_by_gender"):
        return False
    text = timing.apply_pronunciations(text, config_model.get_setting("tts_pronunciations"))
    speed = _engine_rate_percent() / 100.0
    try:
        engine_choice = config_model.get_setting("tts_engine")
        if engine_choice in CLOUD_ENGINES and config_model.get_setting(f"{engine_choice}_voice_{gender}"):
            return _synthesize_with_cloud(engine_choice, text, output_wav_path, voice_key=f"{engine_choice}_voice_{gender}")
        from . import piper_voices
        voice_id = config_model.get_setting(f"piper_voice_{gender}")
        if voice_id and piper_voices.synthesize(voice_id, text, output_wav_path, speed):
            return True
        voice_id = _onecore_voice_for(gender, _description_language())
        if voice_id:
            return bool(asyncio.run(_synthesize_with_onecore(text, output_wav_path, override_settings={
                "onecore_voice_id": voice_id, "onecore_rate_percent": _engine_rate_percent()})))
    except Exception as e:  # never block an export: the usual voice reads the line instead
        app_logger.warning(f"Gendered voice failed, using the usual voice: {e}")
    return False


def synthesize_description_to_wav(text, output_wav_path, gender=None):
    if gender and synthesize_with_gender(text, output_wav_path, gender):
        return True
    text = timing.apply_pronunciations(text, config_model.get_setting("tts_pronunciations"))
    engine_choice = config_model.get_setting("tts_engine")
    app_logger.info(f"TTS synthesis requested with engine: '{engine_choice}'")

    if engine_choice == "openai":
        return _synthesize_with_openai(text, output_wav_path)
    elif engine_choice == "sapi5":
        return _synthesize_with_sapi5(text, output_wav_path)
    elif engine_choice == "sapi5_32bit":
        return _synthesize_with_sapi5_32bit(text, output_wav_path)
    elif engine_choice == "onecore":
        return asyncio.run(_synthesize_with_onecore(text, output_wav_path))
    elif engine_choice in CLOUD_ENGINES:
        return _synthesize_with_cloud(engine_choice, text, output_wav_path)
    else:
        err_msg = f"Unknown TTS engine selected: '{engine_choice}'"
        app_logger.error(err_msg)
        raise TTSError(err_msg)


CLOUD_ENGINES = ("azure", "google")


def _synthesize_with_cloud(engine_choice, text, output_wav_path, voice_key=None, settings=None):
    """Azure / Google neural voice. settings: the Settings dialog's unsaved values (voice test)."""
    get = (lambda k: settings.get(k)) if settings else config_model.get_setting
    voice = (get(voice_key or f"{engine_choice}_voice") or "").strip()
    app_logger.info(f"Cloud TTS synthesis: engine={engine_choice}, voice={voice}")  # never the key
    try:
        if engine_choice == "azure":
            return cloud_tts.synthesize_azure(text, output_wav_path, (get("user_azure_api_key") or "").strip(),
                                              get("azure_region"), voice)
        return cloud_tts.synthesize_google(text, output_wav_path, (get("user_google_tts_api_key") or "").strip(), voice)
    except cloud_tts.CloudTTSError as e:
        app_logger.error(f"Cloud TTS error ({engine_choice}): {e}")
        raise TTSError(f"{engine_choice.capitalize()} speech synthesis failed: {e}")

def _synthesize_with_openai(text, output_wav_path, override_settings=None):
    if not OPENAI_AVAILABLE:
        raise TTSError("OpenAI library is not installed. Please install it with 'pip install openai'.")
    
    if override_settings:
        api_key = override_settings.get("openai_api_key")
        preset_name = override_settings.get("openai_voice_preset")
        model = override_settings.get("openai_tts_model", "gpt-4o-mini-tts")
    else:
        api_key = config_model.get_setting("user_openai_api_key")
        preset_name = config_model.get_setting("openai_tts_voice")
        model = config_model.get_setting("openai_tts_model")

    if not api_key:
        raise TTSError("OpenAI API key is not configured.")

    voice_preset = voice_model.get_voice_by_name(preset_name)
    if not voice_preset:
        raise TTSError(f"Selected voice preset '{preset_name}' not found.")

    base_voice = voice_preset.get("base_voice", "alloy")
    instructions = voice_preset.get("instructions")
    speed = voice_preset.get("speed", 1.0)
    
    try:
        client = OpenAI(api_key=api_key)
        app_logger.info(f"Synthesizing with OpenAI TTS. Preset: {preset_name}, Base Voice: {base_voice}, Model: {model}, Speed: {speed}")
        temp_mp3_path = os.path.join(tempfile.gettempdir(), f"openai_tts_{os.urandom(4).hex()}.mp3")

        request_args = {"model": model, "voice": base_voice, "input": text, "speed": speed, "response_format": "mp3"}
        if instructions:
            request_args["instructions"] = instructions

        with client.audio.speech.with_streaming_response.create(**request_args) as response:
            response.stream_to_file(temp_mp3_path)
        
        if not FFMPEG_IS_AVAILABLE:
            raise TTSError("FFmpeg is required to convert OpenAI MP3 to WAV.")

        ffmpeg_cmd = [FFMPEG_COMMAND, "-y", "-i", temp_mp3_path, output_wav_path]
        process = run_command(ffmpeg_cmd)
        
        if os.path.exists(temp_mp3_path):
            os.remove(temp_mp3_path)

        if process.returncode != 0:
            raise TTSError(f"FFmpeg failed to convert MP3 to WAV: {process.stderr}")

        if os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 44:
            return True
        return False
    except Exception as e:
        app_logger.error(f"OpenAI TTS Error: {e}", exc_info=True)
        raise TTSError(f"OpenAI speech synthesis failed: {e}")

def _synthesize_with_sapi5(text, output_wav_path):
    if not PYTTSX3_AVAILABLE:
        raise TTSError("pyttsx3 library is not available for SAPI5.")
    engine = None
    try:
        engine = pyttsx3.init(driverName='sapi5')
        if not engine:
            raise TTSError("Failed to initialize pyttsx3 engine.")
        voice_id = config_model.get_setting("sapi5_voice_id")
        rate_percent = int(config_model.get_setting("sapi5_voice_rate_percent"))
        base_sapi_rate = 180
        actual_rate = int(base_sapi_rate * (rate_percent / 100.0))
        actual_rate = max(50, min(actual_rate, 450))
        if voice_id:
            try: engine.setProperty('voice', voice_id)
            except Exception as e_voice: app_logger.error(f"SAPI5: Could not set voice ID '{voice_id}': {e_voice}.")
        engine.setProperty('rate', actual_rate)
        output_dir = os.path.dirname(output_wav_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        engine.save_to_file(text, output_wav_path)
        engine.runAndWait()
        if os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 44:
            return True
        return False
    except Exception as e:
        app_logger.error(f"SAPI5: Error during speech synthesis: {e}", exc_info=True)
        raise TTSError(f"SAPI5 speech synthesis failed: {e}")
    finally:
        if engine:
            try: engine.stop()
            except Exception as e_stop: app_logger.error(f"Error stopping SAPI5 engine: {e_stop}")

def _test_voice_onecore(text, settings):
    if not WIN_ONECORE_AVAILABLE:
        raise TTSError("Windows OneCore libraries (winsdk) not available.")

    temp_wav_path = os.path.join(tempfile.gettempdir(), f"test_voice_onecore_{os.urandom(4).hex()}.wav")
    try:
        success = asyncio.run(_synthesize_with_onecore(text, temp_wav_path, override_settings=settings))
        if success:
            sound_player.play(temp_wav_path, is_full_path=True)
    finally:
        if os.path.exists(temp_wav_path):
            time.sleep(1)
            try:
                os.remove(temp_wav_path)
            except Exception as e:
                app_logger.error(f"Could not clean up temp OneCore test file: {e}")

async def _synthesize_with_onecore_inner(text, voice_id, rate):
    synth = speechsynthesis.SpeechSynthesizer()
    
    # Set default language in case voice is not found
    language = "en-US"
    
    if voice_id:
        selected_voice = next((v for v in speechsynthesis.SpeechSynthesizer.all_voices if v.id == voice_id), None)
        if selected_voice:
            synth.voice = selected_voice
            language = selected_voice.language
            app_logger.info(f"OneCore: Using voice '{selected_voice.display_name}' with language '{language}'")
        else:
            app_logger.warning(f"OneCore: Voice with ID '{voice_id}' not found, using default voice")
    else:
        app_logger.info("OneCore: No voice ID specified, using default voice")
    
    # Rate is a multiplier from 0.5 to 6.0; rate is already percent/100,
    # so 100% -> 1.0 (B3: it used to be doubled).
    actual_rate = max(0.5, min(rate, 6.0))
    synth.options.speaking_rate = actual_rate
    app_logger.info(f"OneCore: Setting speaking rate to {actual_rate}")

    # Ensure text is properly escaped for SSML
    escaped_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    ssml = f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{language}"><p>{escaped_text}</p></speak>'
    
    app_logger.info(f"OneCore: Synthesizing SSML: {ssml}")
    
    result = await synth.synthesize_ssml_to_stream_async(ssml)

    return result

async def _synthesize_with_onecore(text, output_wav_path, override_settings=None):
    if not WIN_ONECORE_AVAILABLE:
        raise TTSError("Windows OneCore libraries (winsdk) are not available.")

    if override_settings:
        voice_id = override_settings.get("onecore_voice_id")
        rate_percent = override_settings.get("onecore_rate_percent", 100)
        rate = rate_percent / 100.0
    else:
        voice_id = config_model.get_setting("onecore_voice_id")
        rate_percent = config_model.get_setting("onecore_voice_rate_percent") or 100
        rate = rate_percent / 100.0

    # Validate inputs
    if not text or not text.strip():
        app_logger.warning("OneCore: Empty or whitespace-only text provided for synthesis")
        # Create a silent wav file
        with open(output_wav_path, "wb") as f:
            # Write a minimal WAV header for a silent file
            f.write(b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00')
        return True

    app_logger.info(f"OneCore: Synthesizing text with voice_id='{voice_id}', rate_percent={rate_percent}")

    try:
        result = await _synthesize_with_onecore_inner(text, voice_id, rate)

        if result and result.size > 0:
            with open(output_wav_path, "wb") as f:
                data_reader = streams.DataReader(result)
                await data_reader.load_async(result.size)
                buffer = data_reader.read_buffer(result.size)
                f.write(buffer)
            
            if os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 44:
                app_logger.info(f"OneCore: Successfully synthesized audio to {output_wav_path}")
                return True
            else:
                app_logger.error(f"OneCore: Failed to write valid audio file to {output_wav_path}")
                return False
        else:
            app_logger.error(f"OneCore synthesis failed to produce a valid audio stream.")
            return False
            
    except Exception as e:
        app_logger.error(f"Windows OneCore TTS Error: {e}", exc_info=True)
        raise TTSError(f"Windows OneCore speech synthesis failed: {e}")
    return False

def _synthesize_with_sapi5_32bit(text, output_wav_path, override_settings=None):
    if not SAPI5_32BIT_HELPER_AVAILABLE:
        raise TTSError("32-bit SAPI5 helper executable not found.")

    if override_settings:
        voice_id = override_settings.get("sapi5_voice_id")
        rate_percent = override_settings.get("sapi5_rate_percent", 100)
    else:
        voice_id = config_model.get_setting("sapi5_voice_id")
        rate_percent = int(config_model.get_setting("sapi5_voice_rate_percent"))
    
    base_sapi_rate = 180
    actual_rate = int(base_sapi_rate * (rate_percent / 100.0))
    actual_rate = max(50, min(actual_rate, 450))

    payload = {
        "text": text,
        "output_path": output_wav_path,
        "voice_id": voice_id,
        "rate": actual_rate
    }
    
    try:
        process = subprocess.Popen(
            [SAPI5_32BIT_HELPER_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        _, stderr = process.communicate(input=json.dumps(payload), timeout=60)

        if process.returncode != 0:
            raise TTSError(f"32-bit helper failed: {stderr}")

        if os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 44:
            return True
        return False
    except subprocess.TimeoutExpired:
        raise TTSError("32-bit helper timed out during synthesis.")
    except Exception as e:
        app_logger.error(f"SAPI5 32-bit: Error during speech synthesis: {e}", exc_info=True)
        raise TTSError(f"SAPI5 32-bit speech synthesis failed: {e}")

def get_available_sapi5_voices():
    if not PYTTSX3_AVAILABLE: return []
    voices_list = []
    engine = None 
    try:
        engine = pyttsx3.init(driverName='sapi5')
        if not engine: return []
        voices_props = engine.getProperty('voices')
        if voices_props:
            for vp in voices_props:
                voices_list.append({"name": vp.name, "id": vp.id})
        return voices_list
    except Exception as e:
        app_logger.error(f"Failed to get SAPI5 voices: {e}", exc_info=True)
        return []
    finally:
        if engine:
            try: engine.stop()
            except Exception: pass

def get_available_sapi5_voices_32bit():
    if not SAPI5_32BIT_HELPER_AVAILABLE:
        return []
    try:
        process = subprocess.run(
            [SAPI5_32BIT_HELPER_PATH, "--list-voices"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        return json.loads(process.stdout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        app_logger.error(f"Failed to get 32-bit SAPI5 voices from helper: {e}")
        return []
def get_available_onecore_voices():
    if not WIN_ONECORE_AVAILABLE:
        return []
    
    voices_list = []
    try:
        all_voices = speechsynthesis.SpeechSynthesizer.all_voices
        for voice in all_voices:
            voices_list.append({
                "name": f"{voice.display_name} ({voice.language})",
                "id": voice.id,
                "language": voice.language
            })
        return voices_list
    except Exception as e:
        app_logger.error(f"Failed to get Windows OneCore voices: {e}", exc_info=True)
        return []