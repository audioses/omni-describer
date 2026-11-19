def call_if_open(window, fn, *args, **kwargs):
    """wx.CallAfter for worker threads: runs fn on the UI thread only if window still exists and is
    not closing, so a late callback never touches a destroyed dialog, player or VLC instance."""
    import wx

    def run():
        if window and not getattr(window, "_is_closing", False):
            fn(*args, **kwargs)
    wx.CallAfter(run)
