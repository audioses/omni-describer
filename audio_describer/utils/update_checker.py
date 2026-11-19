# audio_describer/utils/update_checker.py
import wx
import urllib.request
import json
import os
import sys
import tempfile
import zipfile
import hashlib
import subprocess
from packaging.version import parse
from ..utils.logger import app_logger
from ..utils import sound_player
from ..utils import update_signature
from .. import config # <-- FIX: Added import for config to get the new UPDATER_URL

def get_app_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        import __main__
        return os.path.abspath(__main__.__file__)

def get_app_dir():
    return os.path.dirname(get_app_path())

HTTP_HEADERS = {"User-Agent": "OmniDescriber-Updater"}  # GitHub answers 403 without one


def expected_sha256(release_json, download_url):
    """SHA256 GitHub publishes for the release asset ("digest": "sha256:<hex>"), or None."""
    name = download_url.rstrip("/").rsplit("/", 1)[-1]
    for asset in release_json.get("assets") or []:
        digest = asset.get("digest") or ""
        if asset.get("name") == name and digest.startswith("sha256:"):
            return digest.split(":", 1)[1].lower()
    return None


def check_signature(sha256_hex, url):
    """Raise unless url + '.sig' signs this digest (only when the app ships a signing key)."""
    if not config.UPDATE_SIGNING_PUBLIC_KEY:
        return
    req = urllib.request.Request(url + ".sig", headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        signature = response.read(4096).decode("ascii", "replace")
    update_signature.verify_digest(config.UPDATE_SIGNING_PUBLIC_KEY, sha256_hex, signature)


def validate_update_zip(zip_path):
    """Reject corrupt archives and entries that would land outside the app folder (zip-slip)."""
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            raise ValueError(f"Corrupt file in update archive: {bad}")
        for name in zf.namelist():
            norm = os.path.normpath(name.replace("\\", "/"))
            if os.path.isabs(norm) or norm.startswith("..") or ":" in norm:
                raise ValueError(f"Unsafe path in update archive: {name}")


def check_for_updates(parent_frame, current_version, version_url, download_url, lang_code, _):
    try:
        # version_url points at the GitHub "latest release" API, which returns JSON.
        req = urllib.request.Request(version_url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
        remote_version = data.get("tag_name", "").lstrip("v").strip()
        if remote_version and parse(remote_version) > parse(current_version):
            sha = expected_sha256(data, download_url)
            updater_sha = expected_sha256(data, config.UPDATER_URL)
            wx.CallAfter(_show_update_prompt, parent_frame, remote_version, current_version, download_url, lang_code, _, sha,
                         updater_sha)
    except Exception as e:
        app_logger.error(f"Update check failed: {e}", exc_info=True)

def _show_update_prompt(parent_frame, remote_version, current_version, download_url, lang_code, _, sha256=None,
                        updater_sha256=None):
    sound_player.play("update.mp3")
    msg = (_("A new version ({}) is available. You have version {}.\n\n"
           "Would you like to download and install it now?").format(remote_version, current_version))
    title = _("Update Available")
    user_choice = wx.MessageBox(msg, title, wx.YES_NO | wx.ICON_QUESTION, parent=parent_frame)
    if user_choice == wx.YES:
        _initiate_update_and_exit(parent_frame, download_url, lang_code, _, sha256, updater_sha256)


def install_pending_updater(app_dir=None):
    """The updater cannot overwrite itself while running, so it leaves updater.exe.new;
    swap it in here, while nothing runs updater.exe."""
    app_dir = app_dir or get_app_dir()
    pending = os.path.join(app_dir, "updater.exe.new")
    if os.path.exists(pending):
        try:
            os.replace(pending, os.path.join(app_dir, "updater.exe"))
            app_logger.info("Installed the new updater.")
        except OSError as e:
            app_logger.warning(f"Could not install updater.exe.new: {e}")


def verify_updater_bytes(data, sha256=None):
    """Raise unless the downloaded updater.exe matches GitHub's digest or carries a valid signature.
    Unlike the zip, an .exe with neither check is refused: we are about to run it."""
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and digest != sha256:
        raise ValueError("updater.exe checksum mismatch")
    if not sha256 and not config.UPDATE_SIGNING_PUBLIC_KEY:
        raise ValueError("no published checksum or signature for updater.exe")
    check_signature(digest, config.UPDATER_URL)


def _initiate_update_and_exit(parent_frame, download_url, lang_code, _, sha256=None, updater_sha256=None):
    progress_dialog = wx.ProgressDialog(
        _("Downloading Update"), _("Downloading..."), 
        maximum=100, parent=parent_frame, style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE
    )
    try:
        temp_dir = tempfile.gettempdir()
        zip_path = os.path.join(temp_dir, "update.zip")
        hasher = hashlib.sha256()
        req = urllib.request.Request(download_url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as response, open(zip_path, 'wb') as out_file:
            total_size = int(response.info().get('Content-Length', -1))
            bytes_so_far = 0
            chunk_size = 8192
            while True:
                chunk = response.read(chunk_size)
                if not chunk: break
                out_file.write(chunk)
                hasher.update(chunk)
                bytes_so_far += len(chunk)
                if total_size > 0:
                    percent = int(bytes_so_far * 100 / total_size)
                    progress_dialog.Update(percent)
        progress_dialog.Destroy()
        progress_dialog = None

        if sha256 and hasher.hexdigest() != sha256:
            os.remove(zip_path)
            raise ValueError(_("The downloaded update is damaged or was modified (checksum mismatch). It was deleted."))
        if not sha256:
            app_logger.warning("No SHA256 digest published for the update asset; integrity relies on HTTPS only.")
        try:
            check_signature(hasher.hexdigest(), download_url)
        except Exception as e:
            app_logger.error(f"Update signature check failed: {e}")
            os.remove(zip_path)
            raise ValueError(_("The update is not signed by the Omni Describer team (missing or invalid signature). "
                               "It was deleted. Download the new version from the official site instead."))
        validate_update_zip(zip_path)

        app_dir = get_app_dir()
        app_path = get_app_path()
        updater_path = os.path.join(app_dir, "updater.exe")
        install_pending_updater(app_dir)

        # --- START: FIX for missing updater component ---
        if not os.path.exists(updater_path):
            app_logger.warning("Updater component not found at '%s'. Attempting to download it.", updater_path)
            
            # Inform the user that a required component is being downloaded.
            wx.MessageBox(
                _("The updater component seems to be missing. The application will now attempt to download the necessary updater to proceed."),
                _("Updater Missing"),
                wx.OK | wx.ICON_INFORMATION,
                parent_frame
            )
            
            try:
                # Assumes UPDATER_URL is defined in your config.py, e.g., "https://yoursite.com/updater.exe"
                req = urllib.request.Request(config.UPDATER_URL, headers=HTTP_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as response:
                    updater_bytes = response.read()
                # An .exe we are about to run: verify before it touches the disk.
                verify_updater_bytes(updater_bytes, updater_sha256)
                with open(updater_path, 'wb') as out_file:
                    out_file.write(updater_bytes)

                app_logger.info("Successfully downloaded standalone updater to '%s'", updater_path)

                # Final check to ensure the file was actually created before proceeding.
                if not os.path.exists(updater_path):
                    raise IOError("Updater file not found even after a successful download attempt.")

            except Exception as e:
                # If the download fails, show a detailed error and stop the update process.
                app_logger.error("Failed to download the standalone updater: %s", e, exc_info=True)
                wx.MessageBox(
                    _("Could not download the required updater component. The update cannot proceed.\n\nPlease try again later or contact support.\n\nError: {}").format(e),
                    _("Update Error"),
                    wx.OK | wx.ICON_ERROR,
                    parent_frame
                )
                return # Stop the update.
        # --- END: FIX ---

        # If we reach this point, updater_path is guaranteed to exist.
        parent_pid = os.getpid()
            
        command = [
            updater_path, # No need for the complex ternary logic if we always expect an .exe
            zip_path, 
            app_dir, 
            app_path, 
            lang_code,
            str(parent_pid)
        ]
        
        subprocess.Popen(command)
        
        app_logger.info("Update initiated. Triggering fast app exit.")
        app = wx.GetApp()
        app.is_updating = True
        parent_frame.Close()
    except Exception as e:
        if progress_dialog: progress_dialog.Destroy()
        wx.MessageBox(_("Failed to download or apply update: {}").format(e), _("Update Error"), wx.OK | wx.ICON_ERROR, parent_frame)