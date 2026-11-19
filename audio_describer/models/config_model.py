# audio_describer/models/config_model.py
import json
import os
import base64
from audio_describer import config

# --- Obfuscation Key ---
# Static app-level key for XOR obfuscation of settings.
# Not real encryption, but prevents casual reading and automated
# malware scanning for API key patterns in plain text files.
# This replaces the old machine-tied Fernet encryption which broke
# when hardware/OS changed and caused the _rust.pyd crash on some systems.
_OBFUSCATION_KEY = b"OmniDescriberSettingsKey2026!@#xK9"

# --- File Paths ---
if os.name == 'nt':
    APP_DATA_BASE_DIR = os.environ.get('APPDATA', os.path.expanduser("~"))
    APP_DATA_DIR = os.path.join(APP_DATA_BASE_DIR, config.APP_NAME_BASE_UNTRANSLATED)
else:
    APP_DATA_BASE_DIR = os.path.expanduser("~")
    APP_DATA_DIR = os.path.join(APP_DATA_BASE_DIR, ".config", config.APP_NAME_BASE_UNTRANSLATED.lower().replace(" ", "_"))

try:
    if not os.path.exists(APP_DATA_DIR):
        os.makedirs(APP_DATA_DIR, exist_ok=True)
except Exception as e:
    print(f"CRITICAL: Could not create app data directory at {APP_DATA_DIR}. Error: {e}")
    APP_DATA_DIR = os.getcwd()

# New file extension to avoid conflicts with old encrypted files
CONFIG_FILE_PATH = os.path.join(APP_DATA_DIR, "settings.dat2")
# Old paths for migration
_OLD_CONFIG_FILE_PATH = os.path.join(APP_DATA_DIR, "settings.dat")
_OLD_SALT_FILE_PATH = os.path.join(APP_DATA_DIR, "key.salt")
_OLD_PLAIN_CONFIG_PATH = os.path.join(APP_DATA_DIR, "settings.dat.json")

DEFAULT_SETTINGS = {
    "user_gemini_api_key": "",
    "gemini_description_verbosity": config.DEFAULT_VERBOSITY,
    "gemini_model_override": "",
    "gemini_disable_safety_block_none": True,
    "gemini_temperature": 0.3,
    "user_openai_api_key": "",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "alloy",
    "youtube_download_quality": "360p",
    "application_language": "",
    "logging_level": "INFO",
    "license_accepted": False,
    "player_allow_speech_interruption": False,
    "send_silenced_video_to_ai": False,
    "frame_rate_for_ai": 0,
    "enable_video_chunking": False,
    "video_chunk_duration_seconds": 600,
    "enable_character_glossary": False,
    "tts_engine": "sapi5",
    "sapi5_voice_id": None,
    "sapi5_voice_rate_percent": 100,
    "sapi5_voice_volume": 1.0,
    "onecore_voice_id": None,
    "onecore_voice_rate_percent": 100,
    "mp3_audio_ducking_level_db": -9,  # player and export (Netflix: 6-12 dB, never mute)
    "player_volume_percent": 50,
    "player_ad_enabled": True,
    # inline (ducking) | extended (WCAG 1.2.7 pause when it does not fit) | on_demand (Enter / Shift+Enter)
    "player_ad_mode": "inline",
    "tts_pronunciations": "",               # "word = how to say it" per line
    "recent_projects": [],                  # most recent first (File > Recent Projects)
    "player_output_mode": "screen_reader",  # screen_reader | app_voice | braille (audit §6.2)
    "player_overlay": False,                # large description text on the video (low vision)
    "player_overlay_size": 40,
    "player_overlay_color": "yellow",       # yellow | white | cyan
    # Description language, independent of the interface ("" = same as interface).
    "description_language": "",
    "gemini_thinking_level": "medium",
    # Personalisation axes validated with blind users (ViDscribe, audit §10.4).
    "description_style": "objective",       # objective | subjective
    "description_emphasis": "general",      # general | characters | actions | environment | text
    "mention_colors": True,
    "description_explicitation": "name",    # describe | name | explain (ADLAB §1.5)
    "enable_quality_check": True,           # second text-only AI pass that flags uncertain descriptions
    "max_words_per_description": 40,        # about 15 s at 160 wpm (ACB); 0 = only the pause budget
    "ad_voice_gain_db": 0,                  # AD voice level in exports (Ofcom 4.17: separate from programme)
    "translate_foreign_dialogue": False,
    "dialogue_voice_by_gender": True,       # translated dialogue: voice matching the speaker's gender
    "piper_voice_female": "",               # local Piper voice id, "" = an installed Windows voice
    "piper_voice_male": "",
    # Cloud neural voices (tts_engine azure | google), plain REST. *_female / *_male: translated dialogue.
    "user_azure_api_key": "",
    "azure_region": "",                     # e.g. westeurope
    "azure_voice": "",                      # e.g. fr-FR-DeniseNeural
    "azure_voice_female": "",
    "azure_voice_male": "",
    "user_google_tts_api_key": "",
    "google_voice": "",                     # e.g. fr-FR-Chirp3-HD-Aoede
    "google_voice_female": "",
    "google_voice_male": "",
    "on_screen_text_second_voice": True,    # exports: on-screen text read in a lower voice
    "prompt_defaults_version": 0,           # default presets already merged into the user's prompts.json
    # Local pause detection before the AI call (ffmpeg silencedetect).
    "enable_silence_detection": True,
    "silence_threshold_db": -35,
    "silence_min_duration_sec": 0.8,
    "webapp_banner_shown": False,
}


# --- XOR Obfuscation (pure Python, no native dependencies) ---

def _xor_bytes(data, key):
    """XOR data with a repeating key."""
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


_DPAPI_PREFIX = b"DPAPI1:"


def _dpapi(data, protect):
    """Windows DPAPI (CryptProtectData/CryptUnprotectData) for the current user.
    Real encryption tied to the Windows account, no native dependency (ctypes)."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = BLOB(len(data), buf), BLOB()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.GetLastError(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _obfuscate(json_string):
    """Protect the settings JSON (it holds API keys): DPAPI on Windows, XOR elsewhere.
    On Windows a DPAPI failure fails the save (the caller reports it) rather than writing the keys
    with the fixed XOR key, which is no protection."""
    raw_bytes = json_string.encode('utf-8')
    if os.name == 'nt':
        return _DPAPI_PREFIX + base64.b64encode(_dpapi(raw_bytes, True))
    return base64.b64encode(_xor_bytes(raw_bytes, _OBFUSCATION_KEY))


def _deobfuscate(encoded_data):
    """Reads both DPAPI files and legacy XOR files (re-saved with DPAPI on next save)."""
    if encoded_data.startswith(_DPAPI_PREFIX):
        return _dpapi(base64.b64decode(encoded_data[len(_DPAPI_PREFIX):]), False).decode('utf-8')
    xored = base64.b64decode(encoded_data)
    raw_bytes = _xor_bytes(xored, _OBFUSCATION_KEY)
    return raw_bytes.decode('utf-8')


# --- Migration from old Fernet-encrypted settings ---

def _try_migrate_old_settings():
    """Attempts to migrate settings from old formats to the new one.

    Migration priority:
    1. Old Fernet-encrypted settings.dat (try to decrypt with cryptography lib)
    2. Old plain JSON settings.dat.json
    3. Give up and return None

    Returns the migrated settings dict, or None if migration failed/not needed.
    """
    migrated = None

    # Try old Fernet-encrypted file first
    if os.path.exists(_OLD_CONFIG_FILE_PATH):
        migrated = _try_decrypt_old_fernet()
        if migrated is not None:
            print("Successfully migrated settings from old encrypted format.")

    # Try old plain JSON fallback file
    if migrated is None and os.path.exists(_OLD_PLAIN_CONFIG_PATH):
        try:
            with open(_OLD_PLAIN_CONFIG_PATH, 'r', encoding='utf-8') as f:
                migrated = json.load(f)
            print("Successfully migrated settings from old plain JSON format.")
        except Exception as e:
            print(f"Could not read old plain JSON settings: {e}")

    if migrated is not None:
        # Clean up old files
        for old_path in [_OLD_CONFIG_FILE_PATH, _OLD_SALT_FILE_PATH, _OLD_PLAIN_CONFIG_PATH]:
            _safe_rename_old_file(old_path)

    return migrated


def _try_decrypt_old_fernet():
    """Try to decrypt old Fernet-encrypted settings. Returns dict or None."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import platform
        import uuid
        import subprocess

        # Reconstruct old machine-tied key
        identifier_parts = [
            platform.node(), platform.processor(), str(uuid.getnode())
        ]
        if os.name == 'nt':
            try:
                output = subprocess.check_output(['vol', 'C:'], shell=True, text=True)
                serial = output.split("is ")[-1].strip()
                identifier_parts.append(serial)
            except Exception:
                pass
        password = "".join(identifier_parts).encode('utf-8')

        if not os.path.exists(_OLD_SALT_FILE_PATH):
            return None
        with open(_OLD_SALT_FILE_PATH, 'rb') as f:
            salt = f.read()

        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
        key = base64.urlsafe_b64encode(kdf.derive(password))
        fernet = Fernet(key)

        with open(_OLD_CONFIG_FILE_PATH, 'rb') as f:
            encrypted_data = f.read()

        decrypted_data = fernet.decrypt(encrypted_data)
        return json.loads(decrypted_data.decode('utf-8'))
    except Exception as e:
        print(f"Could not decrypt old Fernet settings (expected if hardware changed): {e}")
        return None


def _safe_rename_old_file(file_path):
    """Rename old file to .migrated to avoid re-processing."""
    if os.path.exists(file_path):
        try:
            migrated_path = file_path + ".migrated"
            if os.path.exists(migrated_path):
                os.remove(migrated_path)
            os.rename(file_path, migrated_path)
        except Exception:
            pass


# --- Core Settings API ---

def save_settings(settings_dict):
    """Save settings to disk with XOR obfuscation."""
    try:
        json_string = json.dumps(settings_dict, indent=4)
        obfuscated_data = _obfuscate(json_string)
        # Atomic write: a crash mid-write must not corrupt settings (and lose API keys).
        tmp_path = CONFIG_FILE_PATH + ".tmp"
        with open(tmp_path, 'wb') as f:
            f.write(obfuscated_data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_FILE_PATH)
        return True
    except Exception as e:
        print(f"Failed to save settings: {e}")
        return False


app_settings = None
_settings_load_warning = None  # (English message, detail or None); translated by get_load_warning()


def N_(text):
    """Marks text for translation (extracted into the catalogs) without translating it yet."""
    return text


def load_settings():
    """Load settings from disk, with migration from old formats.

    Returns a settings dict. Never raises - falls back to defaults on any error.
    Sets _settings_load_warning if there was a recoverable issue.
    """
    global app_settings, _settings_load_warning
    _settings_load_warning = None

    # Try loading from new format first
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, 'rb') as f:
                encoded_data = f.read()
            json_string = _deobfuscate(encoded_data)
            settings = json.loads(json_string)
            settings = _apply_defaults_and_cleanup(settings)
            return settings
        except Exception as e:
            print(f"Settings file corrupted (error: {e}). Attempting migration or reset.")
            _settings_load_warning = (N_("Settings file was corrupted and has been reset. Your previous settings could not be recovered. (Error: %s)"), str(e))
            try:
                os.rename(CONFIG_FILE_PATH, CONFIG_FILE_PATH + ".corrupted")
            except Exception:
                pass

    # Try migrating from old format
    migrated = _try_migrate_old_settings()
    if migrated is not None:
        settings = _apply_defaults_and_cleanup(migrated)
        # Save in new format immediately
        save_settings(settings)
        return settings

    # Check if old files exist but couldn't be migrated
    if os.path.exists(_OLD_CONFIG_FILE_PATH):
        _settings_load_warning = (N_(
            "Your previous settings could not be migrated from the old encrypted format. "
            "This typically happens after a Windows update or hardware change. "
            "Settings have been reset to defaults. You will need to re-enter your API keys."), None)
        _safe_rename_old_file(_OLD_CONFIG_FILE_PATH)
        _safe_rename_old_file(_OLD_SALT_FILE_PATH)

    return DEFAULT_SETTINGS.copy()


def _apply_defaults_and_cleanup(settings):
    """Ensure all default keys exist and remove obsolete keys."""
    updated = False
    for key, default_value in DEFAULT_SETTINGS.items():
        if key not in settings:
            settings[key] = default_value
            updated = True
    obsolete_keys = [
        "sapi5_voice_rate", "gemini_top_p", "gemini_top_k",
        "gemini_max_output_tokens",
        # vision backends other than Gemini were removed; drop their settings and the stored API key
        "image_backend", "video_backend", "ollama_model", "vision_base_url", "vision_model", "user_vision_api_key",
    ]
    for key in obsolete_keys:
        if key in settings:
            del settings[key]
            updated = True
    if updated:
        save_settings(settings)
    return settings


def get_setting(key_name):
    """Get a setting value by key. Loads settings on first access."""
    global app_settings
    if app_settings is None:
        app_settings = load_settings()
    if key_name not in app_settings:
        default_val = DEFAULT_SETTINGS.get(key_name)
        if default_val is None and key_name not in DEFAULT_SETTINGS:
            return None
        app_settings[key_name] = default_val
    return app_settings.get(key_name)


def get_load_warning():
    """Warning about settings loading, in the interface language, or None. Settings load before
    translations exist, so the English text is kept (N_) and translated here, when shown."""
    if not _settings_load_warning:
        return None
    from ..i18n_setup import _
    text, detail = _settings_load_warning
    return _(text) % detail if detail is not None else _(text)


def remember_recent_project(path, limit=8):
    """Put a project at the top of File > Recent Projects and save the settings."""
    global app_settings
    if app_settings is None:
        app_settings = load_settings()
    key = os.path.normcase(os.path.abspath(path))
    recent = [p for p in (app_settings.get("recent_projects") or []) if os.path.normcase(os.path.abspath(p)) != key]
    app_settings["recent_projects"] = [os.path.abspath(path)] + recent[:limit - 1]
    save_settings(app_settings)


def system_language():
    """Windows display language as a two-letter code (fr, en...)."""
    import locale
    try:
        import ctypes
        name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
    except (AttributeError, OSError):  # not Windows
        name = os.environ.get("LANG", "")
    return (name.split("_")[0].split(".")[0] or "en").lower()


def ui_language():
    """Interface language: the user's choice, else Windows' language, read again at every start (not
    saved, so changing the Windows language changes the app's)."""
    return (get_setting("application_language") or "").strip() or system_language()
