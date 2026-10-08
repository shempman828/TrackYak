"""Tests for the StatusManager status bus in src/foundation/status_utility.py."""

from PySide6.QtCore import QThread

from src.foundation import status_utility
from src.foundation.status_utility import StatusManager


def _drain(qapp, predicate, timeout_ms=2000):
    """Process events until `predicate()` is true or the timeout passes."""
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
    return predicate()


def _record(signal):
    events = []
    signal.connect(lambda *args: events.append(args))
    return events


def _reset():
    StatusManager._active_tasks = 0
    StatusManager._task_message = ""
    StatusManager._auto_hide_timer.stop()


def test_constructor_returns_same_initialised_singleton(qapp):
    _reset()
    StatusManager._active_tasks = 3
    again = status_utility._StatusManager()
    assert again is StatusManager
    assert again._active_tasks == 3  # __init__ must not reset state
    _reset()


def test_show_message_from_worker_thread_runs_on_manager_thread(qapp):
    _reset()
    shown = _record(StatusManager.show_status)

    class Worker(QThread):
        def run(self):
            StatusManager.show_message("from worker", 5000)

    worker = Worker()
    worker.start()
    worker.wait()

    assert _drain(qapp, lambda: ("from worker", 5000) in shown)
    # The QTimer could only start because the call ran on the manager's own thread.
    assert StatusManager._auto_hide_timer.isActive()
    _reset()


def test_task_message_returns_after_transient_message_expires(qapp):
    _reset()
    shown = _record(StatusManager.show_status)
    hidden = _record(StatusManager.hide_status)

    StatusManager.start_task("Analysing 10 tracks")
    StatusManager.show_message("Analysis resumed", 10)

    assert _drain(qapp, lambda: shown and shown[-1] == ("Analysing 10 tracks", 0))
    assert hidden == []

    StatusManager.end_task()
    assert hidden
    _reset()
