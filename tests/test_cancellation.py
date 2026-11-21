"""Escape cancellation: app_state helpers, the Gemini retry loop and the upload (no network, no UI)."""
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

try:
    import wx  # noqa: F401
except ImportError:  # i18n_setup imports wx; the logic under test does not need it
    sys.modules.setdefault("audio_describer.i18n_setup", types.SimpleNamespace(_=lambda s: s))

from audio_describer import app_state
from audio_describer.core import audio_describer
from audio_describer.core import gemini_helpers as g


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _CancelTest(unittest.TestCase):
    def setUp(self):
        self.event = threading.Event()
        app_state.set_cancel_event(self.event)

    def tearDown(self):
        app_state.set_cancel_event(None)


class AppStateTest(_CancelTest):
    def test_without_event_calls_run_inline(self):
        app_state.set_cancel_event(None)
        self.assertEqual(app_state.run_cancellable(lambda x: x * 2, 21), 42)
        self.assertFalse(app_state.cancel_requested())

    def test_blocking_call_is_abandoned_on_cancel(self):
        threading.Timer(0.1, self.event.set).start()
        started = time.monotonic()
        with self.assertRaises(app_state.GenerationCancelled):
            app_state.run_cancellable(time.sleep, 5)
        self.assertLess(time.monotonic() - started, 2)

    def test_errors_are_reraised_on_caller_thread(self):
        with self.assertRaises(ZeroDivisionError):
            app_state.run_cancellable(lambda: 1 / 0)

    def test_abandoned_call_hands_its_result_to_on_abandoned(self):
        release, late = threading.Event(), []
        threading.Timer(0.1, self.event.set).start()
        with self.assertRaises(app_state.GenerationCancelled):
            app_state.run_cancellable(lambda: release.wait(5) and "uploaded", on_abandoned=late.append)
        self.assertEqual(late, [])  # still running
        release.set()
        self.assertTrue(_wait_until(lambda: late == ["uploaded"]), late)

    def test_on_abandoned_not_called_when_not_cancelled(self):
        late = []
        self.assertEqual(app_state.run_cancellable(lambda: "ok", on_abandoned=late.append), "ok")
        self.assertEqual(late, [])

    def test_sleep_or_cancel(self):
        app_state.sleep_or_cancel(0.01)  # not cancelled: returns
        self.event.set()
        with self.assertRaises(app_state.GenerationCancelled):
            app_state.sleep_or_cancel(5)

    def test_event_is_per_thread(self):
        self.event.set()
        seen = []
        t = threading.Thread(target=lambda: seen.append(app_state.cancel_requested()))
        t.start()
        t.join()
        self.assertEqual(seen, [False])
        self.assertTrue(app_state.cancel_requested())


class _Client:
    def __init__(self, on_call):
        self.calls, self.on_call = 0, on_call
        self.models = self

    def generate_content(self, model=None, **kwargs):
        self.calls += 1
        return self.on_call()


class RetryTest(_CancelTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(g, "_lazy_import_gemini_sdk", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _generate(self, client):
        return g.generate_content_with_retry(client, "m", [], None)

    def test_no_request_starts_after_cancel(self):
        self.event.set()
        client = _Client(lambda: "ok")
        with self.assertRaises(app_state.GenerationCancelled):
            self._generate(client)
        self.assertEqual(client.calls, 0)

    def test_cancel_during_backoff_stops_retries(self):
        def rate_limited():
            self.event.set()  # Escape while the request is out
            raise Exception("429 RESOURCE_EXHAUSTED")
        client = _Client(rate_limited)
        with self.assertRaises(app_state.GenerationCancelled):
            self._generate(client)
        self.assertEqual(client.calls, 1)

    def test_cancel_during_request_is_not_wrapped_or_retried(self):
        client = _Client(lambda: time.sleep(5))
        threading.Timer(0.1, self.event.set).start()
        with self.assertRaises(app_state.GenerationCancelled):
            self._generate(client)
        self.assertEqual(client.calls, 1)


class _Files:
    def __init__(self, state="ACTIVE", block=None):
        self.state, self.block = state, block
        self.uploaded_path, self.deleted = None, []

    def upload(self, file):
        self.uploaded_path = file
        if self.block is not None:
            self.block.wait(5)
        return types.SimpleNamespace(name="files/1", state=types.SimpleNamespace(name=self.state))

    def get(self, name):
        return types.SimpleNamespace(name=name, state=types.SimpleNamespace(name=self.state))

    def delete(self, name):
        self.deleted.append(name)


class UploadTest(_CancelTest):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.video = os.path.join(self.dir, "vidéo.mp4")  # non-ASCII: uploaded from a temp copy
        with open(self.video, "wb") as f:
            f.write(b"x" * 16)

    def test_cancel_during_upload_deletes_late_upload_and_temp_copy(self):
        release = threading.Event()
        files = _Files(block=release)
        client = types.SimpleNamespace(files=files)
        threading.Timer(0.1, self.event.set).start()
        with self.assertRaises(app_state.GenerationCancelled):
            audio_describer._upload_and_wait_for_active(client, self.video)
        temp_copy = files.uploaded_path
        self.assertNotEqual(temp_copy, self.video)
        self.assertTrue(os.path.exists(temp_copy))  # still held by the upload thread
        release.set()  # the upload finishes after the user gave up on it
        self.assertTrue(_wait_until(lambda: files.deleted == ["files/1"] and not os.path.exists(temp_copy)))

    def test_cancel_while_waiting_for_active_deletes_upload(self):
        files = _Files(state="PROCESSING")
        client = types.SimpleNamespace(files=files)
        threading.Timer(0.1, self.event.set).start()
        with self.assertRaises(app_state.GenerationCancelled):
            audio_describer._upload_and_wait_for_active(client, self.video)
        self.assertEqual(files.deleted, ["files/1"])
        self.assertFalse(os.path.exists(files.uploaded_path))

    def test_normal_upload_keeps_file_and_removes_temp_copy(self):
        files = _Files()
        client = types.SimpleNamespace(files=files)
        self.assertEqual(audio_describer._upload_and_wait_for_active(client, self.video).name, "files/1")
        self.assertEqual(files.deleted, [])
        self.assertFalse(os.path.exists(files.uploaded_path))


if __name__ == "__main__":
    unittest.main()
