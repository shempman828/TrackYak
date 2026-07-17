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
