# audio_describer/models/prompt_model.py
import json
import os
# from . import config_model  <- This was causing the circular import.
from .. import config
from ..utils.logger import app_logger

PROMPTS_FILE_NAME = "prompts.json"
PROMPTS_FILE_PATH = None # Initialize as None

# In-memory cache for prompts
_prompts_cache = {}

def _get_prompts_file_path():
    """Gets the prompts file path, importing config_model locally."""
    global PROMPTS_FILE_PATH
    if PROMPTS_FILE_PATH is None:
        # Import locally to break the circular dependency at startup.
        from . import config_model
        PROMPTS_FILE_PATH = os.path.join(config_model.APP_DATA_DIR, PROMPTS_FILE_NAME)
    return PROMPTS_FILE_PATH


def _get_default_prompts_for_lang(lang_code):
    """Returns a copy of the default prompts for a specific language."""
    # Languages without their own presets get the English ones (the AI still writes in the description language).
    return list(DEFAULT_PROMPTS.get(lang_code) or DEFAULT_PROMPTS.get("en", []))

def load_prompts():
    """Loads all prompts from the JSON file into the cache."""
    global _prompts_cache
    
    file_path = _get_prompts_file_path()

    if not os.path.exists(file_path):
        app_logger.info(f"Prompts file not found at {file_path}. Creating with defaults.")
        _prompts_cache = DEFAULT_PROMPTS.copy()
        save_prompts()
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            _prompts_cache = json.load(f)
        # Ensure all default languages are present
        updated = False
        for lang_code, default_list in DEFAULT_PROMPTS.items():
            if lang_code not in _prompts_cache:
                _prompts_cache[lang_code] = default_list.copy()
                updated = True
        updated |= _merge_new_defaults()
        updated |= sum(_repair_hint_text(v) for v in _prompts_cache.values() if isinstance(v, list)) > 0
        if updated:
            save_prompts()
        app_logger.info(f"Prompts loaded successfully from {file_path}.")
    except (json.JSONDecodeError, IOError) as e:
        app_logger.error(f"Error loading prompts from {file_path}: {e}. Using default prompts.")
        _prompts_cache = DEFAULT_PROMPTS.copy()

# Bump when presets are added to DEFAULT_PROMPTS so existing users receive them once.
PROMPT_DEFAULTS_VERSION = 4


# SHA-256 (first 16 hex) of default preset texts replaced in v4 (examples starting with a pronoun,
# typos). A user preset still holding one of these exact texts was never edited, so it is
# upgraded to the current default of the same name; edited presets are never touched.
_SUPERSEDED_DEFAULT_HASHES = frozenset({
    'af968ffa01ae43ea', '53d94dd9b7fb42d1', 'f5f32b14fffbf78c', '975297d903354fd8', 'a48c3eb6f72a35ff',
    '02c4b6b4e63ac109', '49458cb69d149c0f', 'becadfd6b05696f9', '60136ba8f7666c67', '340fe84cceb6f806',
    '9cafc4bee5375fa2', '6d3639972c886fc7', 'edb2fc04f6ef6625', 'ac9dab449b85d49a', '25858182b14ca542',
    'd9fb9eb2a2612cad', 'a41c542ba6d2752e', '572e1e8bc26dcc50', '083ee301032b1be1', '6a32cee906c6736a',
    'aca413102e8e948e', 'e9ed7287321c3a61',
})


def _text_hash(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _upgrade_unedited_presets(user_list, default_list):
    """Replace untouched old default texts by the current default; returns how many changed."""
    current = {p["name"]: p["prompt"] for p in default_list}
    changed = 0
    for preset in user_list:
        new_text = current.get(preset.get("name"))
        if new_text and preset.get("prompt") != new_text and _text_hash(preset.get("prompt") or "") in _SUPERSEDED_DEFAULT_HASHES:
            preset["prompt"] = new_text
            changed += 1
    return changed


# Older versions wrote the fields' accessible names into the fields themselves (TextCtrl.SetLabel),
# so saved presets can start with these hints. English + current UI language are stripped on load.
_FIELD_HINTS = ("A short, descriptive name for the prompt preset.", "The full text of the prompt to be sent to the AI.")


def _repair_hint_text(presets):
    """Remove field hints glued into preset names/texts by the old dialog bug; returns how many fixed."""
    hints = set(_FIELD_HINTS)
    try:
        from ..i18n_setup import _
        hints |= {_(h) for h in _FIELD_HINTS}
    except Exception:  # no wx (tests, CLI without UI): English only
        pass
    fixed = 0
    for preset in presets:
        for key in ("name", "prompt"):
            value = preset.get(key) or ""
            cleaned = value
            for hint in hints:
                cleaned = cleaned.replace(hint, "")
            if cleaned != value and cleaned.strip():
                preset[key] = cleaned.strip()
                fixed += 1
    return fixed


def _merge_new_defaults():
    """Add default presets the user has never seen (by name). Runs once per version,
    so a preset the user deletes afterwards does not come back."""
    from . import config_model
    if (config_model.get_setting("prompt_defaults_version") or 0) >= PROMPT_DEFAULTS_VERSION:
        return False
    added = 0
    for lang_code, default_list in DEFAULT_PROMPTS.items():
        user_list = _prompts_cache.setdefault(lang_code, [])
        added += _upgrade_unedited_presets(user_list, default_list)
        names = {p.get("name") for p in user_list}
        for preset in default_list:
            if preset["name"] not in names:
                user_list.append(dict(preset))
                added += 1
    if config_model.app_settings is not None:
        config_model.app_settings["prompt_defaults_version"] = PROMPT_DEFAULTS_VERSION
        config_model.save_settings(config_model.app_settings)
    app_logger.info(f"Merged {added} new default prompt presets.")
    return added > 0


def save_prompts():
    """Saves the current state of the prompts cache to the JSON file."""
    global _prompts_cache
    
    file_path = _get_prompts_file_path()

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(_prompts_cache, f, indent=4, ensure_ascii=False)
        app_logger.info(f"Prompts saved successfully to {file_path}.")
        return True
    except IOError as e:
        app_logger.error(f"Error saving prompts to {file_path}: {e}")
        return False

def get_prompts_for_language(lang_code):
    """
    Returns a list of prompt dictionaries for a given language.
    Returns a default list if the language is not found.
    """
    if not _prompts_cache:
        load_prompts()
    return _prompts_cache.get(lang_code, _get_default_prompts_for_lang(lang_code))

def set_prompts_for_language(lang_code, prompts_list):
    """
    Sets the entire list of prompts for a language and saves.
    """
    if not isinstance(prompts_list, list):
        app_logger.error(f"set_prompts_for_language: provided prompts is not a list for lang '{lang_code}'.")
        return False

    _prompts_cache[lang_code] = prompts_list
    return save_prompts()


MAX_PRESETS, MAX_PRESET_CHARS = 200, 20000


def validate_presets(data):
    """Imported preset file (untrusted): a list of {name, prompt} strings. Returns clean copies or raises ValueError."""
    if not isinstance(data, list) or not data or len(data) > MAX_PRESETS:
        raise ValueError("expected a list of 1 to %d presets" % MAX_PRESETS)
    out = []
    for item in data:
        name = item.get("name") if isinstance(item, dict) else None
        prompt = item.get("prompt") if isinstance(item, dict) else None
        if not (isinstance(name, str) and isinstance(prompt, str) and name.strip() and prompt.strip()):
            raise ValueError("each preset needs a text name and prompt")
        if len(name) > 200 or len(prompt) > MAX_PRESET_CHARS:
            raise ValueError("preset too long: %s" % name[:50])
        out.append({"name": name.strip(), "prompt": prompt.strip()})
    return out


def _load_default_presets():
    """Default presets live in prompts/<language>.json, one file per language (fr.json, en.json...).
    Translating or adding a language is adding a file there; no code change."""
    folder = config.get_prompts_dir()
    presets = {}
    for file_name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        lang, ext = os.path.splitext(file_name)
        if ext.lower() != ".json":
            continue
        try:
            with open(os.path.join(folder, file_name), encoding="utf-8") as f:
                presets[lang.lower()] = validate_presets(json.load(f))
        except (OSError, ValueError, AttributeError) as e:  # a broken translation must not stop the app
            app_logger.error(f"Default presets {file_name} skipped: {e}")
    if "en" not in presets:
        app_logger.error(f"No English default presets found in {folder}.")
    return presets


DEFAULT_PROMPTS = _load_default_presets()
