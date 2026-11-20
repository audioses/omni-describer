# updater.py
import sys
import os
import zipfile
import time
import subprocess
import tempfile
import gettext
import logging
import shutil
import ctypes
from ctypes import wintypes

# --- WIN32 API DEFINITIONS ---
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
WaitForSingleObject.restype = wintypes.DWORD

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL

# --- LOGGING SETUP ---
def setup_updater_logger(log_dir):
    log_file = os.path.join(log_dir, "updater_log.txt")
    logger = logging.getLogger("Updater")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        ch = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger

def wait_for_process_to_exit(pid, logger):
    logger.info(f"Waiting for main application process (PID: {pid}) to exit...")
    try:
        handle = OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            logger.warning(f"Could not get handle for PID {pid}. Assuming it has exited.")
            return
        ret = WaitForSingleObject(handle, 120000)  # a long video export can take a while to close
        if ret == 0:
            logger.info(f"Main application process (PID: {pid}) has exited.")
        else:
            logger.warning(f"Timed out waiting for main application process.")
        CloseHandle(handle)
    except Exception as e:
        logger.error(f"Exception while waiting for process: {e}", exc_info=True)

def main():
    try:
        zip_path = sys.argv[1]
        extract_dir = sys.argv[2]
        app_to_relaunch = sys.argv[3]
        lang_code = sys.argv[4]
        parent_pid = int(sys.argv[5])
    except (IndexError, ValueError):
        sys.exit(1)

    logger = setup_updater_logger(extract_dir)
    logger.info("="*30); logger.info("Updater Final Stage Started.")

    temp_dir = tempfile.gettempdir()
    is_frozen = getattr(sys, 'frozen', False)
    running_from_temp = temp_dir in os.path.abspath(sys.executable)

    if not running_from_temp and is_frozen:
        temp_updater_path = os.path.join(temp_dir, os.path.basename(sys.executable))
        try:
            shutil.copy2(sys.executable, temp_updater_path)
            subprocess.Popen([temp_updater_path] + sys.argv[1:])
            sys.exit(0)
        except Exception as e:
            logger.error(f"Failed to relocate updater: {e}", exc_info=True); sys.exit(1)
    
    os.chdir(temp_dir)
    logger.info(f"Updater running from temp directory: {os.getcwd()}")
    
    wait_for_process_to_exit(parent_pid, logger)

    for attempt in range(5):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                logger.info(f"Attempt {attempt + 1}: Starting extraction from {zip_path}")
                
                # --- THE FINAL, DEFINITIVE FIX ---
                # Extract all files EXCEPT the updater itself.
                # Stage the new updater with a .new extension.
                updater_exe_name = "updater.exe" # The name of the updater in the zip
                new_updater_path = os.path.join(extract_dir, updater_exe_name + ".new")
                
                for member in zip_ref.infolist():
                    member_filename = os.path.basename(member.filename)
                    
                    if member_filename.lower() == updater_exe_name.lower():
                        # This is the new updater. Extract it with the .new extension.
                        try:
                            with open(new_updater_path, "wb") as f_out:
                                f_out.write(zip_ref.read(member.filename))
                            logger.info(f"  -> Staged new updater to: {new_updater_path}")
                        except Exception as e:
                            logger.error(f"  ** FAILED to stage new updater: {e} **")
                            raise # Re-raise to trigger the retry
                    else:
                        # Extract all other files normally.
                        zip_ref.extract(member, extract_dir)
                # --- END OF THE FIX ---

            logger.info("Extraction successful.")
            os.remove(zip_path)
            
            logger.info(f"Relaunching application: {app_to_relaunch}")
            subprocess.Popen([app_to_relaunch])
            
            logger.info("Updater has completed its task.")
            sys.exit(0)

        except PermissionError:
            logger.warning(f"Permission error on attempt {attempt + 1}. Another process may be interfering. Waiting...")
            time.sleep(2) # A longer sleep between retries now
        except Exception as e:
            logger.error(f"Fatal error during extraction: {e}", exc_info=True)
            break

    logger.error("Update failed. A file in the application directory remains locked or the archive is unusable.")
    try:  # never leave the user without their app
        subprocess.Popen([app_to_relaunch])
    except OSError as e:
        logger.error(f"Could not relaunch the application: {e}")
    sys.exit(1)

if __name__ == "__main__":
    main()