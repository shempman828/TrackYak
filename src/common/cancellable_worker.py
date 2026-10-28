"""Base class for QThread workers that support cooperative cancellation."""

from PySide6.QtCore import QThread


class CancellableWorker(QThread):
    """QThread subclass with a cooperative cancel flag."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_cancelled = False

    def request_cancel(self):
        """Request cooperative cancellation; checked via `is_cancelled`."""
        self._is_cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """True once `request_cancel()` was called."""
        return self._is_cancelled

    @staticmethod
    def _release_db_session() -> None:
        """Return this worker thread's pooled DB connection, if run() checked one out."""
        # Call from run()'s `finally` on the worker thread itself: scoped_session
        # keys by thread identity, and a dead thread's session otherwise pins its
        # pooled connection forever (QueuePool TimeoutError once enough pile up).
        from src.db.db_engine import Session

        Session.remove()
