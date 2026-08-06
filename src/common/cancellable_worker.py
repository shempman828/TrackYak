"""Base class for QThread workers that support cooperative cancellation.

Standardizes the cancel flag/method naming -- workers across the codebase
previously used _stop_requested/_is_cancelled/_stop/_cancel paired with
stop()/cancel() inconsistently. Subclasses check `self.is_cancelled` inside
their run()/work loop and return early when it's set; callers call
`request_cancel()`.

Signals and run() are intentionally NOT templated here: payload shapes
differ across workers (int,int vs int,int,str vs int,int,int) and some
workers fold errors into their `finished` payload by design rather than
using a separate `error` signal. This base class only owns the cancel flag.

Any subclass whose run() touches the DB (via `controller.get/add/update/
delete/merge/split`, or a session pulled from one of those) must call
`self._release_db_session()` in a `finally` block before run() returns.
The app's engine uses a `scoped_session` keyed by thread identity, so the
first DB touch on this thread registers a brand-new Session there; a
read-only query never calls commit/rollback/close, so without this the
connection it checked out stays pinned forever once this (now-dead)
thread's registry entry is orphaned. Enough orphaned threads exhaust the
pool (see the QueuePool TimeoutError post-mortem in track_edit_roles.py's
_RolesLoaderWorker, the first place this was caught).
"""

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
        return self._is_cancelled

    @staticmethod
    def _release_db_session() -> None:
        """Return this worker thread's pooled DB connection, if run() checked
        one out. Must be called from run() itself (i.e. on this worker's own
        thread) -- scoped_session keys by thread identity, so calling this
        from any other thread would release the wrong session."""
        from src.db.db_engine import Session

        Session.remove()
