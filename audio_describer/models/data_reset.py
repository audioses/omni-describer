# audio_describer/models/data_reset.py
"""Reset settings and presets to a clean state (no wx: used by the UI and tests).

Every reset first copies what it replaces or deletes into APP_DATA_DIR/backups/<timestamp>/,
so a mistaken reset can be undone by hand. Exported files are never touched."""
import copy
import datetime
import json
import os
import shutil

from . import config_model
from ..utils.logger import app_logger

# Settings kept by a reset unless the user also asks to forget them.
API_KEY_SETTINGS = tuple(k for k in config_model.DEFAULT_SETTINGS if k.startswith("user_") and k.endswith("_api_key"))


def _backup(paths, backup_root=None):
    """Copy existing files into a fresh timestamped backup folder; returns it (or None)."""
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(backup_root or os.path.join(config_model.APP_DATA_DIR, "backups"), stamp)
    os.makedirs(folder, exist_ok=True)
    for path in existing:
        shutil.copy2(path, os.path.join(folder, os.path.basename(path)))
    app_logger.info(f"Backed up {len(existing)} item(s) to {folder}")
    return folder


def reset_settings(keep_api_keys=True):
    """All settings back to defaults; API keys kept unless keep_api_keys is False.
    Prompt presets live in their own file and are not affected."""
    current = config_model.app_settings or {}
    fresh = copy.deepcopy(config_model.DEFAULT_SETTINGS)
    if keep_api_keys:
        for key in API_KEY_SETTINGS:
            if current.get(key):
                fresh[key] = current[key]
    folder = _backup([config_model.CONFIG_FILE_PATH])
    if folder and not keep_api_keys:  # the backup must not keep the keys the user asked to forget
        scrubbed = {k: ("" if k in API_KEY_SETTINGS else v) for k, v in current.items()}
        with open(os.path.join(folder, os.path.basename(config_model.CONFIG_FILE_PATH)), "wb") as f:
            f.write(config_model._obfuscate(json.dumps(scrubbed, indent=4)))
    if not config_model.save_settings(fresh):
        raise OSError("Could not save the reset settings.")
    if config_model.app_settings is not None:
        config_model.app_settings.clear()
        config_model.app_settings.update(fresh)
    else:
        config_model.app_settings = fresh


def reset_prompts():
    """Prompt presets back to the defaults (custom presets removed), and the user's own AD rules
    file (ad_rules.json) removed so the built-in rules apply again."""
    from . import prompt_model
    from ..core.prompts import USER_RULES_FILE
    user_rules = os.path.join(config_model.APP_DATA_DIR, USER_RULES_FILE)
    _backup([prompt_model._get_prompts_file_path(), user_rules])
    if os.path.isfile(user_rules):
        os.remove(user_rules)
    if not prompt_model.reset_prompts():
        raise OSError("Could not save the default prompt presets.")


def reset_voices():
    """OpenAI voice presets back to defaults."""
    from . import voice_model
    _backup([voice_model.VOICES_FILE_PATH])
    if not voice_model.set_voices(copy.deepcopy(voice_model.DEFAULT_VOICES)):
        raise OSError("Could not save the default voice presets.")
