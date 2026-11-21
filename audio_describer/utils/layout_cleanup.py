# audio_describer/utils/layout_cleanup.py
"""Remove what the old flat PyInstaller 5 layout left next to the exe once the app
runs from a PyInstaller 6 build (libraries and data in _internal/).

The updater only extracts over the old files, so the old DLLs, .pyd and bin/vlc stay
at the top, where Windows looks first. Stdlib only: updater.exe imports this too.
"""
import os
import shutil

# User data and updater files that live in the app folder (config.get_app_data_dir,
# logger, run_app, updater, video_processor's cwd-based temp_processing, and
# config_model's cwd fallback for settings). Never deleted, whatever the build contains.
KEEP = {
    "_internal", "updater.exe", "updater.exe.new", "app_log.txt", "updater_log.txt",
    "temp_processing", "debug_ai_outputs", "settings.dat2", "settings.dat",
    "settings.dat.json", "key.salt", "prompts.json", "voices.json",
}
# Old-layout folders that PyInstaller 6 ships under another name (_tcl_data, _tk_data).
RENAMED_IN_V6 = {"tcl", "tk"}
# Package folders the flat v2.1.2 release shipped and later builds no longer bundle
# (googleapiclient/grpc came with the removed google-generativeai SDK): ~110 MB that
# the "shipped in _internal" rule cannot see. Seen in a real update from v2.1.2.
DROPPED_PACKAGES = {"googleapiclient", "grpc", "httplib2", "win32com"}


def stale_flat_layout_entries(top_level, internal):
    """Names in the app folder (top_level) left over from the flat layout.

    A leftover is anything the new build now ships inside _internal/ (internal: the
    names directly in it) -- old DLLs, .pyd, base_library.zip, bin/, locale/, package
    folders -- plus any stray top-level .dll/.pyd. User files with other names are
    never touched. Nothing when there is no _internal/ or the folder is already clean.
    """
    if not internal:
        return []
    internal = {n.lower() for n in internal}
    return sorted(n for n in top_level
                  if n.lower() not in KEEP
                  and (n.lower() in internal or n.lower() in RENAMED_IN_V6 or n.lower() in DROPPED_PACKAGES
                       or n.lower().endswith((".dll", ".pyd", ".dist-info"))))


def remove_stale_flat_layout(app_dir, log):
    """Delete stale_flat_layout_entries() from app_dir. Never raises: a locked or
    undeletable entry is logged and skipped (the next run tries again)."""
    try:
        internal_dir = os.path.join(app_dir, "_internal")
        if not os.path.isdir(internal_dir):
            return
        names = stale_flat_layout_entries(os.listdir(app_dir), os.listdir(internal_dir))
    except OSError as e:
        log.warning(f"Old layout cleanup skipped: {e}")
        return
    for name in names:
        path = os.path.join(app_dir, name)  # listdir names: no separators, stays in app_dir
        try:
            # rmtree refuses a top-level link/junction and never follows nested ones.
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onerror=lambda f, p, e: log.warning(f"Could not remove {p}: {e[1]}"))
            else:
                os.remove(path)
            if not os.path.lexists(path):
                log.info(f"Removed old layout leftover: {path}")
        except OSError as e:
            log.warning(f"Could not remove old layout leftover {path}: {e}")
