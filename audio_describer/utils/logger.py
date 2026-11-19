import logging
import logging.handlers
import os
import sys
from audio_describer import config

_log_file_path = ""


def get_log_dir():
    """Per-user, always-writable log directory.

    %LOCALAPPDATA%\\OmniDescriber\\logs on Windows (the exe folder may be under
    Program Files and read-only); ~/.cache/omni_describer/logs elsewhere.
    """
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser("~")
        log_dir = os.path.join(base, "OmniDescriber", "logs")
    else:
        log_dir = os.path.join(os.path.expanduser("~"), ".cache", "omni_describer", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        import tempfile
        log_dir = tempfile.gettempdir()
    return log_dir


def get_log_file_path():
    """Returns the full path to the application's log file."""
    global _log_file_path
    if not _log_file_path:
        _log_file_path = os.path.join(get_log_dir(), config.LOG_FILE)
    return _log_file_path


def setup_logger():
    """Sets up the application logger with rotation, so a crash log survives the next launch."""
    log_path = get_log_file_path()
    log_format = '%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s'

    # Clear previous handlers to prevent duplication
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.handlers.clear()

    handlers = [logging.StreamHandler()]
    try:
        handlers.insert(0, logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8'))
    except OSError as e:
        print(f"Could not open log file {log_path}: {e}")

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        handlers=handlers,
    )

    logger = logging.getLogger(config.APP_NAME_BASE_UNTRANSLATED)
    logger.info("=" * 20 + " New session " + "=" * 20)
    logger.info(f"Logger initialized. Log file at: {log_path}")
    return logger


def update_log_level():
    """Updates the logger's level based on the current settings."""
    # This now safely imports config_model as there is no circular dependency.
    from audio_describer.models import config_model

    logger = logging.getLogger(config.APP_NAME_BASE_UNTRANSLATED)
    level_str = config_model.get_setting("logging_level") or config.LOG_LEVEL

    if level_str.upper() == "DISABLED":
        logger.disabled = True
    else:
        logger.disabled = False
        level = getattr(logging, level_str.upper(), logging.INFO)
        logger.setLevel(level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)
        logger.info(f"Logger level set to {level_str.upper()}")

app_logger = setup_logger()
