"""Qt side of src/db/db_session_sync.py: run the main-session expire on the main thread."""

import contextlib

from PySide6.QtCore import QObject, Signal, Slot

from src.db import db_session_sync


class MainSessionSync(QObject):
    """Main-thread receiver for "a worker committed a write" notifications."""

    # Emitted from the committing worker thread, so Qt queues _on_stale onto the
    # main thread -- ahead of that worker's own `finished`, since queued events
    # run in order.
    stale = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stale.connect(self._on_stale)

    @Slot()
    def _on_stale(self):
        db_session_sync.expire_main_session_if_stale()


def install_main_session_sync(parent) -> MainSessionSync:
    """Create the receiver on the main thread (owned by `parent`) and register it with db_session_sync."""
    sync = MainSessionSync(parent)

    def notify():
        # Runs inside a worker's commit: never let a torn-down receiver (app
        # shutdown while a worker is still finishing) fail that commit.
        with contextlib.suppress(RuntimeError):
            sync.stale.emit()

    db_session_sync.set_notifier(notify)
    return sync
