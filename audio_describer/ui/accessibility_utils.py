try:



    from accessible_output2.outputs.auto import Auto
    speaker = Auto()
    ACCESSIBLE_OUTPUT_AVAILABLE = True
except ImportError:
    speaker = None
    ACCESSIBLE_OUTPUT_AVAILABLE = False
    print("Warning: accessible-output2 library not found. Screen reader announcements will be disabled.")

def speak_message(message, interrupt=False):
    """
    Speaks a message using the screen reader if available.
    :param message: The string to be spoken.
    :param interrupt: Whether to interrupt current speech.
    """
    if ACCESSIBLE_OUTPUT_AVAILABLE and speaker:
        try:
            speaker.speak(message, interrupt=interrupt)
            # Also show it on the braille display (NVDA/JAWS); outputs without braille raise.
            try:
                speaker.braille(message)
            except Exception:
                pass
        except Exception as e:
            # Fallback or logging if speaking fails
            print(f"Accessibility Speak Error: {e}. Message: {message}")
            # Optionally, you could try a simple print for critical messages
            # as a last resort if the screen reader integration fails.
    else:
        # Fallback for when screen reader output is not available
        print(f"Accessibility (Fallback): {message}")


def braille_message(message):
    """Braille display only, no speech (audit §6.2, braille-only mode)."""
    if ACCESSIBLE_OUTPUT_AVAILABLE and speaker:
        try:
            speaker.braille(message)
            return
        except Exception as e:
            print(f"Accessibility Braille Error: {e}")
    print(f"Accessibility (Fallback): {message}")


def set_control_accessible_name(control, name):
    """
    Sets the accessible name for a wxPython control.
    wxPython controls usually derive their accessible name from their label.
    For controls without visible labels (e.g., buttons with icons only),
    or when a more descriptive name is needed.

    :param control: The wxPython control.
    :param name: The accessible name string.
    """
    import wx
    # TextCtrl.SetLabel asserts on recent wxPython ("Use SetValue()"): its label is its content.
    if hasattr(control, 'SetLabel') and not isinstance(control, wx.TextCtrl) and not control.GetLabel():
        control.SetLabel(name)
    
    # For many wxPython controls, GetAccessible().SetName(name) is the way
    # However, direct access to SetName on the accessible object might vary
    # or require ensuring an accessible object exists.
    # This often works:
    try:
        accessible = control.GetAccessible()
        if accessible:
            accessible.SetName(name)
    except Exception as e:
        print(f"Could not set accessible name for {control}: {e}")
    
    # For some controls, ToolTip can also serve as accessible name if other methods fail
    # control.SetToolTip(name)

# Example of how you might ensure a control is keyboard navigable (usually default)
# def ensure_keyboard_focusable(control):
#     current_style = control.GetWindowStyle()
#     if not (current_style & wx.TAB_TRAVERSAL):
#         control.SetWindowStyle(current_style | wx.TAB_TRAVERSAL)