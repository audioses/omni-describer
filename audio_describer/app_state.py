# audio_describer/app_state.py
import threading

# A thread-safe event object that will be used to signal shutdown to all parts of the application.
# When this event is set, any long-running background tasks should terminate themselves.
shutdown_event = threading.Event()


class GenerationCancelled(Exception):
    """Raised when the user cancels a running generation (Escape)."""


# The cancel event of the generation running on this thread, so blocking steps deep in the
# pipeline (ffmpeg, retry waits, HTTP requests) can stop without passing it through every call.
_local = threading.local()


def set_cancel_event(event):
    _local.cancel_event = event


def cancel_requested():
    event = getattr(_local, "cancel_event", None)
    return shutdown_event.is_set() or (event is not None and event.is_set())


def raise_if_cancelled():
    if cancel_requested():
        raise GenerationCancelled("Generation cancelled by user.")


def sleep_or_cancel(seconds):
    """time.sleep that ends early and raises GenerationCancelled when the user cancels."""
    event = getattr(_local, "cancel_event", None) or shutdown_event
    if event.wait(seconds) or shutdown_event.is_set():
        raise GenerationCancelled("Generation cancelled by user.")


def run_cancellable(fn, *args, poll_sec=0.2, on_abandoned=None, **kwargs):
    """Run a blocking call (HTTP request, SDK call) on a helper thread and return its result, but
    raise GenerationCancelled as soon as the user cancels. The abandoned call still finishes in the
    background; on_abandoned(result) then runs on the helper thread (result is None if the call
    failed), so whatever it created can be cleaned up."""
    event = getattr(_local, "cancel_event", None)
    if event is None:
        return fn(*args, **kwargs)
    box = {}
    lock = threading.Lock()

    def target():
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as e:  # re-raised on the caller's thread
            box["error"] = e
        with lock:
            box["done"] = True
            abandoned = box.get("abandoned", False)
        if abandoned and on_abandoned is not None:
            on_abandoned(box.get("result"))

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    while True:
        worker.join(poll_sec)
        with lock:
            if box.get("done"):
                break
            if event.is_set() or shutdown_event.is_set():
                box["abandoned"] = True
                raise GenerationCancelled("Generation cancelled by user.")
    if "error" in box:
        raise box["error"]
    return box["result"]
