"""Qt side of src/db/db_session_sync.py: run the main-session expire on the main thread.

db_session_sync calls its notifier on the worker thread that committed the
write. Emitting a signal of a QObject that lives on the main thread from there
makes Qt queue the slot onto the main thread's event loop. Because the worker
emits this before it emits its own `finished` signal, and queued events run in
order, every `finished` handler already sees the expired (fresh) session.
"""

import contextlib

from PySide6.QtCore import QObject, Signal, Slot

from src.db import db_session_sync


class MainSessionSync(QObject):
    """Main-thread receiver for "a worker committed a write" notifications."""

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
