# audio_describer/utils/sound_player.py
import os
import threading

try:
    import pygame
    LIBRARIES_AVAILABLE = True
except (ImportError, AttributeError, Exception) as e:
    LIBRARIES_AVAILABLE = False

from .logger import app_logger
from .. import config

MIXER_INITIALIZED = False

def _initialize_mixer():
    global MIXER_INITIALIZED
    if not LIBRARIES_AVAILABLE or MIXER_INITIALIZED:
        return
    try:
        pygame.mixer.init()
        MIXER_INITIALIZED = True
        app_logger.info("SoundPlayer: Pygame mixer initialized successfully.")
    except pygame.error as e:
        app_logger.error(f"Pygame mixer could not be initialized: {e}")
        MIXER_INITIALIZED = False

def _play_in_thread(sound_path):
    """Internal function to play a sound file in a separate thread."""
    try:
        sound = pygame.mixer.Sound(sound_path)
        sound.play()
        while pygame.mixer.get_busy():
            pygame.time.delay(100)
    except Exception as e:
        app_logger.error(f"Failed to play sound: {e}", exc_info=True)

def play_panned(sound_identifier: str, pan: float):
    """Play a notification sound placed in the stereo field (-1 left .. 1 right)."""
    if not MIXER_INITIALIZED:
        return
    sound_path = os.path.join(config.get_notifs_dir(), sound_identifier)
    try:
        channel = pygame.mixer.Sound(sound_path).play()
        if channel:
            pan = max(-1.0, min(1.0, float(pan)))
            channel.set_volume(min(1.0, 1.0 - pan), min(1.0, 1.0 + pan))
    except Exception as e:
        app_logger.error(f"Failed to play panned sound: {e}")


_voice_channel = None


def play_voice(path, volume=1.0):
    """Player app voice: one clip at a time, a new one cuts the previous. Returns False if not played."""
    global _voice_channel
    if not MIXER_INITIALIZED:
        return False
    try:
        stop_voice()
        _voice_channel = pygame.mixer.Sound(path).play()
        if _voice_channel:
            _voice_channel.set_volume(max(0.0, min(1.0, volume)))
        return _voice_channel is not None
    except Exception as e:
        app_logger.error(f"Failed to play voice clip: {e}")
        return False


def stop_voice():
    if _voice_channel:
        _voice_channel.stop()


def play(sound_identifier: str, is_full_path=False):
    """
    Plays a sound in a non-blocking way.

    Notification sounds (e.g. "start.mp3") are loaded straight from the
    notifs/ folder next to the app. Pass is_full_path=True to play an
    arbitrary file (e.g. a generated TTS preview) from its own path instead.
    """
    if not MIXER_INITIALIZED:
        app_logger.warning(f"Skipping playback ('{sound_identifier}') because mixer is not initialized.")
        return

    sound_path = sound_identifier if is_full_path else os.path.join(config.get_notifs_dir(), sound_identifier)

    if not os.path.exists(sound_path):
        app_logger.error(f"Sound file not found at path '{sound_path}'. Cannot play.")
        return

    thread = threading.Thread(target=_play_in_thread, args=(sound_path,), daemon=True)
    thread.start()

# --- Initialize on import ---
_initialize_mixer()
