# audio_describer/models/prompt_model.py
import copy
import json
import os
# from . import config_model  <- This was causing the circular import.
from .. import config
from ..utils.logger import app_logger

PROMPTS_FILE_NAME = "prompts.json"
PROMPTS_FILE_PATH = None # Initialize as None

# Key in prompts.json (next to the language lists) holding the ids of every default preset the user
# has already been offered. Kept with the presets, not in the settings, so a settings reset does not
# offer them again; a preset the user deleted or renamed stays gone.
SEEN_DEFAULTS_KEY = "_seen_default_ids"

# Ids of the default presets built into versions before presets carried ids. A prompts.json without
# SEEN_DEFAULTS_KEY was written by such a version: these were already offered, the others are new.
LEGACY_DEFAULT_IDS = frozenset({
    "movie_trailer", "action_movie", "romantic_movie", "comedy_sitcom", "sci_fi_fantasy",
    "meme_viral_video", "documentary_educational", "cooking_recipe", "diy_tutorial", "gaming_lets_play",
})

MAX_PRESETS, MAX_PRESET_CHARS = 200, 20000


def validate_presets(data):
    """A preset list read from a file: {id, name, prompt} strings. Returns clean copies or raises ValueError."""
    if not isinstance(data, list) or not data or len(data) > MAX_PRESETS:
        raise ValueError("expected a list of 1 to %d presets" % MAX_PRESETS)
    out = []
    for item in data:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip()
                                                 for k in ("id", "name", "prompt")):
            raise ValueError("each preset needs a text id, name and prompt")
        if len(item["name"]) > 200 or len(item["prompt"]) > MAX_PRESET_CHARS:
            raise ValueError("preset too long: %s" % item["name"][:50])
        out.append({k: item[k].strip() for k in ("id", "name", "prompt")})
    return out


def _load_default_presets():
    """Default presets live in prompts/<language>.json (en.json, fr.json...). Adding a language is
    adding a file there; no code change. The same id in two languages is the same preset."""
    folder = config.get_prompts_dir()
    presets = {}
    for file_name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        lang, ext = os.path.splitext(file_name)
        if ext.lower() != ".json":
            continue
        try:
            with open(os.path.join(folder, file_name), encoding="utf-8-sig") as f:
                presets[lang.lower()] = validate_presets(json.load(f))
        except (OSError, ValueError) as e:  # a broken translation must not stop the app
            app_logger.error(f"Default presets {file_name} skipped: {e}")
    if "en" not in presets:
        app_logger.error(f"No English default presets found in {folder}.")
    return presets


DEFAULT_PROMPTS = _load_default_presets()

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


def _all_default_ids():
    return sorted({p["id"] for presets in DEFAULT_PROMPTS.values() for p in presets})


def _get_default_prompts_for_lang(lang_code):
    """Returns a copy of the default prompts for a language; English when it has none
    (the AI still writes in the description language)."""
    return copy.deepcopy(DEFAULT_PROMPTS.get(lang_code) or DEFAULT_PROMPTS.get("en", []))


def default_store():
    """A fresh presets store: every default preset, all marked as offered."""
    store = copy.deepcopy(DEFAULT_PROMPTS)
    store[SEEN_DEFAULTS_KEY] = _all_default_ids()
    return store


def _merge_new_defaults():
    """Offer default presets whose id the user has never been offered, once. Returns True if the
    store changed. Matching is by id, never by name, so deleted or renamed presets stay gone."""
    seen = _prompts_cache.get(SEEN_DEFAULTS_KEY)
    seen = set(seen) if isinstance(seen, list) else set(LEGACY_DEFAULT_IDS)
    changed = SEEN_DEFAULTS_KEY not in _prompts_cache
    for lang_code, default_list in DEFAULT_PROMPTS.items():
        user_list = _prompts_cache.get(lang_code)
        if not isinstance(user_list, list):  # a language the user never had: all its defaults
            _prompts_cache[lang_code] = copy.deepcopy(default_list)
            changed = True
            continue
        new = [copy.deepcopy(p) for p in default_list if p["id"] not in seen]
        if new:
            user_list.extend(new)
            changed = True
            app_logger.info(f"Added {len(new)} new default prompt presets for '{lang_code}'.")
    all_ids = _all_default_ids()
    if not seen.issuperset(all_ids):
        changed = True
    _prompts_cache[SEEN_DEFAULTS_KEY] = sorted(seen.union(all_ids))
    return changed


def load_prompts():
    """Loads all prompts from the JSON file into the cache."""
    global _prompts_cache

    file_path = _get_prompts_file_path()

    if not os.path.exists(file_path):
        app_logger.info(f"Prompts file not found at {file_path}. Creating with defaults.")
        _prompts_cache = default_store()
        save_prompts()
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            _prompts_cache = json.load(f)
        if not isinstance(_prompts_cache, dict):
            raise ValueError("expected a JSON object")
        if _merge_new_defaults():
            save_prompts()
        app_logger.info(f"Prompts loaded successfully from {file_path}.")
    except (ValueError, IOError) as e:
        app_logger.error(f"Error loading prompts from {file_path}: {e}. Using default prompts.")
        _prompts_cache = default_store()

def reset_prompts():
    """Every preset back to the defaults (custom presets removed) and saved."""
    _prompts_cache.clear()
    _prompts_cache.update(default_store())
    return save_prompts()

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
    presets = _prompts_cache.get(lang_code) if lang_code != SEEN_DEFAULTS_KEY else None
    return presets if isinstance(presets, list) else _get_default_prompts_for_lang(lang_code)

def set_prompts_for_language(lang_code, prompts_list):
    """
    Sets the entire list of prompts for a language and saves.
    """
    if not isinstance(prompts_list, list) or lang_code == SEEN_DEFAULTS_KEY:
        app_logger.error(f"set_prompts_for_language: provided prompts is not a list for lang '{lang_code}'.")
        return False

    _prompts_cache[lang_code] = prompts_list
    return save_prompts()
