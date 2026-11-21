# Application Details
import sys
import os

# --- Base Path Calculation ---
# This is the definitive, most robust method for finding data files.
def get_app_root():
    """
    Gets the application's root directory, handling both source and frozen modes.
    """
    if getattr(sys, 'frozen', False):
        # In a frozen app, the data folders (notifs, locale, etc.) are
        # placed next to the main executable. This is the most reliable way.
        return os.path.dirname(sys.executable)
    else:
        # In source mode, data files are in the 'audio_describer' directory,
        # same as this config.py file.
        return os.path.dirname(os.path.abspath(__file__))

# --- Data Directory Functions ---
# These functions call get_app_root() to ensure the path is always correct at runtime.
def get_locale_dir():
    return os.path.join(get_app_root(), 'locale')

def get_notifs_dir():
    return os.path.join(get_app_root(), 'notifs')

def get_doc_dir():
    return os.path.join(get_app_root(), 'doc')

def get_app_data_dir():
    """
    Gets the directory where user-modifiable data files (settings, prompts) are stored.
    For portability, this is the same as the application's root directory.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # In source mode, place it at the project root for easier access.
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


# Define a dummy _ function for gettext string extraction at module import time.
# The real _ function will be initialized in i18n_setup.py later.
def _(text):
    return text

# This is the literal string that will be extracted by gettext for translation.
# It's wrapped by the dummy _() here so xgettext can find it.
APP_NAME_BASE_UNTRANSLATED = _("Describer") 

# This is the core, translatable part of the application name.
# It will be updated by i18n_setup.py with its truly translated value at runtime.
APP_NAME_TRANSLATABLE = APP_NAME_BASE_UNTRANSLATED 

# This is an optional, fixed prefix that is NOT translatable.
# Include a space if you want one between the prefix and the translatable name.
APP_NAME_FIXED_PREFIX = "Omni " 

APP_VERSION = "2.1.0"

# --- Auto-update (GitHub Releases) ---
# The updater reads the latest GitHub release and pulls its assets. The release
# workflow publishes assets under fixed names (omni_describer.zip, updater.exe),
# so these /latest/download/ URLs stay valid across every version.
GITHUB_REPO = "audioses/omni-describer"
VERSION_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/omni_describer.zip"
UPDATER_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/updater.exe"
# Ed25519 public key (base64) that signs every update asset (<asset>.sig). Empty =
# only GitHub's SHA-256 digest is checked. Set it only together with the release
# secret UPDATE_SIGNING_KEY: see audio_describer/utils/update_signature.py.
UPDATE_SIGNING_PUBLIC_KEY = ""
WEBSITE_URL = "https://audioses.com/"
WEBAPP_URL = "https://studio.binclusive.io"
CONTACT_EMAIL = "info@audioses.com"

# API Settings (Placeholder - Store securely, not directly in code for production)
GEMINI_API_KEY = None
# Gemini model to use. "gemini-flash-latest" tracks the newest Flash release, so retired models (e.g. 2.5) no longer 404.
# Pin a specific model (e.g. "gemini-3.8-flash") via the "Gemini Model Override" setting.
GEMINI_MODEL_NAME = "gemini-flash-latest"

# Video Processing
SUPPORTED_VIDEO_FORMATS = "*.mp4;*.avi;*.mkv;*.webm;*.mov;*.flv;*.wmv;*.mpeg;*.mpg;*.3gp;*.ogv;*.ts;*.m4v;*.divx"
DEFAULT_YOUTUBE_RESOLUTION = "780p" # or a low-resolution format like 'worst'

# UI Settings
DEFAULT_WINDOW_SIZE = (800, 600)

# Paths (could be dynamically determined as well)
# For now, let's assume user settings might be stored in a user-specific directory
# This would be handled more robustly by config_model.py
# Use the untranslated base name for file system paths, as these should be stable.
USER_DATA_DIR_NAME = "omniDescriber"
TEMP_DIR_NAME = "temp_processing"

# Verbosity Levels (Example)
VERBOSITY_SHORT = "short"
VERBOSITY_STANDARD = "standard"
VERBOSITY_DETAILED = "detailed"
DEFAULT_VERBOSITY = VERBOSITY_STANDARD

# Logging
LOG_FILE = "app_log.txt"
LOG_LEVEL = "INFO" # e.g., DEBUG, INFO, WARNING, ERROR