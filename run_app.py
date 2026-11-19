# run_app.py
import sys
import os
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
            base_path = os.path.dirname(sys.executable)
            sys.path.insert(0, base_path)
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, base_path)
        
        # --- Stage 2: Run the main application with full logging ---
        # Any error from here on will be caught and logged by the configured app_logger.
        
        if len(sys.argv) > 1 and sys.argv[1] == "--cli":
            # Command line in the compiled app (audit §10): OmniDescriber.exe --cli film.mp4 --export vtt
            if getattr(sys, 'frozen', False) and os.name == 'nt':
                import ctypes
                if ctypes.windll.kernel32.AttachConsole(-1):  # windowed exe: write to the calling console
                    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                    sys.stderr = sys.stdout
            from audio_describer.cli import main as cli_main
            sys.exit(cli_main(sys.argv[2:]))

        # We pre-import the logger here to make it available in the final catch block.
        from audio_describer.utils.logger import app_logger, get_log_file_path

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