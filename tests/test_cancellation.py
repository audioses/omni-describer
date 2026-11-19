"""Escape stops blocking steps: thread-local cancel event in app_state (stdlib only)."""
import threading
import time
import unittest

from audio_describer import app_state


class CancellationTest(unittest.TestCase):
    def tearDown(self):
        app_state.set_cancel_event(None)

    def test_without_event_calls_run_inline(self):
        self.assertEqual(app_state.run_cancellable(lambda x: x * 2, 21), 42)
        self.assertFalse(app_state.cancel_requested())

    def test_blocking_call_is_abandoned_on_cancel(self):
        event = threading.Event()
        app_state.set_cancel_event(event)
        threading.Timer(0.1, event.set).start()
        started = time.monotonic()
        with self.assertRaises(app_state.GenerationCancelled):
            app_state.run_cancellable(time.sleep, 5)
        self.assertLess(time.monotonic() - started, 2)

    def test_errors_are_reraised_on_caller_thread(self):
        app_state.set_cancel_event(threading.Event())
        with self.assertRaises(ZeroDivisionError):
            app_state.run_cancellable(lambda: 1 / 0)

    def test_sleep_or_cancel(self):
        event = threading.Event()
        app_state.set_cancel_event(event)
        app_state.sleep_or_cancel(0.01)  # not cancelled: returns
        event.set()
        with self.assertRaises(app_state.GenerationCancelled):
            app_state.sleep_or_cancel(5)

    def test_event_is_per_thread(self):
        event = threading.Event()
        event.set()
        app_state.set_cancel_event(event)
        seen = []
        t = threading.Thread(target=lambda: seen.append(app_state.cancel_requested()))
        t.start()
        t.join()
        self.assertEqual(seen, [False])
        self.assertTrue(app_state.cancel_requested())


if __name__ == "__main__":
    unittest.main()
