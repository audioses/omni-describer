# audio_describer/utils/system_utils.py
import subprocess
import time
import threading
from .logger import app_logger
from .. import app_state
import os
import sys

_FFMPEG_PATH = None
_FFPROBE_PATH = None

def get_ffmpeg_path(tool='ffmpeg'):
    """
    Finds and caches the path to the specified tool (ffmpeg or ffprobe).
    Prioritizes the bundled version if available, otherwise falls back to the system path.
    """
    global _FFMPEG_PATH, _FFPROBE_PATH

    # Determine which cached path to check/set
    cached_path = _FFMPEG_PATH if tool == 'ffmpeg' else _FFPROBE_PATH
    if cached_path:
        return cached_path

    command_name = tool
    executable_name = f"{tool}.exe" if os.name == 'nt' else tool

    # Check for a bundled version first
    if getattr(sys, 'frozen', False):
        # Running as a bundled executable (e.g., PyInstaller)
        if hasattr(sys, '_MEIPASS'):
            # Temporary directory for PyInstaller
            base_dir = sys._MEIPASS
        else:
            # Standard executable location
            base_dir = os.path.dirname(sys.executable)
    else:
        # Running from source, determine path relative to this file
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    bundled_path = os.path.join(base_dir, 'bin', executable_name)
    if os.path.exists(bundled_path):
        app_logger.info(f"Found bundled {tool} at: {bundled_path}")
        if tool == 'ffmpeg': _FFMPEG_PATH = bundled_path
        else: _FFPROBE_PATH = bundled_path
        return bundled_path

    # Fallback to system path if no bundled version is found
    app_logger.warning(f"No bundled {tool} found at '{bundled_path}'. Relying on system PATH.")
    if tool == 'ffmpeg': _FFMPEG_PATH = command_name
    else: _FFPROBE_PATH = command_name
    return command_name

def _reader_thread(pipe, output_list):
    """A simple thread function to read lines from a process pipe."""
    try:
        for line in iter(pipe.readline, ''):
            output_list.append(line)
    finally:
        pipe.close()

def run_command(command):
    """
    Runs a command line process and captures its output in a non-blocking way
    that prevents I/O deadlocks. It is also aware of the application's shutdown
    event and will terminate the subprocess if a shutdown is signaled.
    """
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creation_flags
        )

        stdout_output = []
        stderr_output = []

        # Create and start daemon threads to read stdout and stderr
        stdout_thread = threading.Thread(target=_reader_thread, args=(process.stdout, stdout_output))
        stderr_thread = threading.Thread(target=_reader_thread, args=(process.stderr, stderr_output))
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        while process.poll() is None:
            if app_state.cancel_requested():  # app closing, or Escape on the generation running on this thread
                app_logger.warning(f"Stop requested. Terminating process: {' '.join(command)}")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    app_logger.warning("Process did not terminate gracefully. Forcing kill.")
                    process.kill()
                
                # Wait for reader threads to finish
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=-1,
                    stdout="".join(stdout_output),
                    stderr="".join(stderr_output) + "\nProcess terminated by application shutdown."
                )
            time.sleep(0.1) # Non-blocking wait

        # Process finished on its own, wait for threads to catch all output
        stdout_thread.join()
        stderr_thread.join()
        
        return subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout="".join(stdout_output),
            stderr="".join(stderr_output)
        )

    except FileNotFoundError:
        app_logger.error(f"Command not found: {command[0]}")
        return subprocess.CompletedProcess(args=command, returncode=1, stdout='', stderr='File not found')
    except Exception as e:
        app_logger.error(f"Error running command '{' '.join(command)}': {e}")
        return subprocess.CompletedProcess(args=command, returncode=1, stdout='', stderr=str(e))