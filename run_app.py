# run_app.py
import sys
import os


class _EarlyLog:
    """Collects the cleanup's messages until the app logger exists."""
    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(("info", msg))

    def warning(self, msg):
        self.lines.append(("warning", msg))


# After an update from a flat (PyInstaller 5) release, the old DLLs and .pyd are still
# next to the exe, and Windows loads DLLs from there first. Remove them before anything
# (tkinter, wx, asyncio...) loads one and locks it.
_early_log = _EarlyLog()
if getattr(sys, "frozen", False) and os.path.basename(getattr(sys, "_MEIPASS", "")) == "_internal":
    try:
        from audio_describer.utils.layout_cleanup import remove_stale_flat_layout
        remove_stale_flat_layout(os.path.dirname(sys.executable), _early_log)
    except Exception as e:  # never block startup over cleanup
        _early_log.warning(f"Old layout cleanup failed: {e}")

import traceback
import time
import tkinter as tk
from tkinter import messagebox

def _show_early_error_popup(title, message):
    """Shows a simple, dependency-free error message."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()

def main():
    """
    The main entry point for the application, with robust two-stage crash handling.
    """
    try:
        # --- Stage 1: Initial Setup (before logger is fully configured) ---
        if getattr(sys, 'frozen', False):
            # _internal/ on PyInstaller 6 (the exe folder on 5). Never the exe folder on 6:
            # old-layout modules left there would shadow the bundled ones.
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            sys.path.insert(0, base_path)
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, base_path)
        
        # --- Stage 2: Run the main application with full logging ---
        # Any error from here on will be caught and logged by the configured app_logger.
        
        # We pre-import the logger here to make it available in the final catch block.
        from audio_describer.utils.logger import app_logger, get_log_file_path
        for level, msg in _early_log.lines:
            getattr(app_logger, level)(msg)

        try:
            # --- Pre-warm Critical Imports ---
            # The explicit 'import google.generativeai' has been removed to allow for
            # lazy loading, which prevents startup crashes on some systems.

            # --- Run the Main Application Logic ---
            from audio_describer.__app__real import main as run_main_app
            run_main_app()

        except Exception as e:
            # --- THIS IS THE "BLACK BOX" CRASH HANDLER ---
            # If we reach here, the logger is guaranteed to be initialized.
            crash_report = (
                f"\n\n--- UNHANDLED EXCEPTION ---\n"
                f"Omni Describer encountered a fatal error and could not continue.\n"
                f"Please send the complete contents of this log file to the support team.\n"
                f"Log file path: {get_log_file_path()}\n"
                f"--- START OF CRASH REPORT ---\n"
            )
            
            app_logger.critical(crash_report, exc_info=True)
            
            # Show a user-friendly message pointing them to the log.
            _show_early_error_popup(
                "Application Startup Error",
                "Omni Describer encountered a critical error during startup and had to close.\n\n"
                "A detailed error report has been saved to the application's log file.\n\n"
                f"Please find the log file at:\n{get_log_file_path()}\n\n"
                "And send it to the support team for assistance."
            )
            sys.exit(1)

    except Exception as early_e:
        # --- This is the FALLBACK handler for very early crashes ---
        # This runs if something goes wrong before the main logger is even set up.
        # We write a raw report to a file in the executable's directory.
        error_time = time.strftime('%Y-%m-%d_%H-%M-%S')
        crash_file_path = os.path.join(os.path.dirname(sys.executable), f"early_crash_{error_time}.txt")
        
        with open(crash_file_path, "w", encoding="utf-8") as f:
            f.write(f"--- EARLY STARTUP CRASH ---\n")
            f.write("The application failed before the main logger could be initialized.\n")
            f.write(traceback.format_exc())

        _show_early_error_popup(
            "Fatal Early Startup Error",
            f"Omni Describer failed very early during startup.\n\n"
            f"A crash report has been saved next to the application executable:\n{crash_file_path}\n\n"
            "Please send this file to the support team."
        )
        sys.exit(1)

if __name__ == "__main__":
    main()