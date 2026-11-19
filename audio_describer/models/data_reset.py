# audio_describer/models/data_reset.py
"""Reset settings, presets and downloaded data to a clean state (no wx: used by the UI and tests).

Every reset first copies what it replaces or deletes into APP_DATA_DIR/backups/<timestamp>/,
so a mistaken reset can be undone by hand. Projects (.omniad) and exported files are never touched."""
import copy
import datetime
import os
import shutil
import tempfile

from . import config_model
from ..utils.logger import app_logger

# Settings kept by a reset unless the user also asks to forget them.
API_KEY_SETTINGS = tuple(k for k in config_model.DEFAULT_SETTINGS if k.startswith("user_") and k.endswith("_api_key"))


def _local_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    return os.path.join(base, "OmniDescriber") if base else config_model.APP_DATA_DIR


def _backup(paths, backup_root=None):
    """Copy existing files/folders into a fresh timestamped backup folder; returns it (or None)."""
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(backup_root or os.path.join(config_model.APP_DATA_DIR, "backups"), stamp)
    os.makedirs(folder, exist_ok=True)
    for path in existing:
        target = os.path.join(folder, os.path.basename(path))
        if os.path.isdir(path):
            shutil.copytree(path, target, dirs_exist_ok=True)
        else:
            shutil.copy2(path, target)
    app_logger.info(f"Backed up {len(existing)} item(s) to {folder}")
    return folder


def reset_settings(keep_api_keys=True):
    """All settings back to defaults; API keys kept unless keep_api_keys is False."""
    current = config_model.app_settings or {}
    fresh = copy.deepcopy(config_model.DEFAULT_SETTINGS)
    if keep_api_keys:
        for key in API_KEY_SETTINGS:
            if current.get(key):
                fresh[key] = current[key]
    _backup([config_model.CONFIG_FILE_PATH])
    if not config_model.save_settings(fresh):
        raise OSError("Could not save the reset settings.")
    if config_model.app_settings is not None:
        config_model.app_settings.clear()
        config_model.app_settings.update(fresh)
    else:
        config_model.app_settings = fresh


def reset_prompts():
    """Prompt presets back to the defaults of every language (custom presets removed)."""
    from . import prompt_model
    _backup([prompt_model._get_prompts_file_path()])
    prompt_model._prompts_cache.clear()
    prompt_model._prompts_cache.update(copy.deepcopy(prompt_model.DEFAULT_PROMPTS))
    if not prompt_model.save_prompts():
        raise OSError("Could not save the default prompt presets.")


def reset_voices():
    """OpenAI voice presets back to defaults."""
    from . import voice_model
    _backup([voice_model.VOICES_FILE_PATH])
    voice_model.set_voices(copy.deepcopy(voice_model.DEFAULT_VOICES))


def downloaded_data_paths():
    """Re-downloadable data: Piper voices and the speech-detection model."""
    local = _local_data_dir()
    return [os.path.join(local, "piper"), os.path.join(local, "silero_vad_v5.onnx")]


def clear_downloaded_data():
    """Delete downloaded voices/models (fetched again when needed). Not backed up: they are re-downloadable."""
    removed = 0
    for path in downloaded_data_paths():
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                removed += 1
            elif os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            app_logger.warning(f"Could not remove {path}: {e}")
    return removed


def clear_temporary_files():
    """The app's own temporary folder (downloads, processed videos). Never anything else in %TEMP%."""
    folder = os.path.join(tempfile.gettempdir(), "OmniDescriber")
    shutil.rmtree(folder, ignore_errors=True)
    return folder
