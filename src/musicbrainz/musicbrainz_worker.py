"""Background QThread that runs one MusicBrainz client call off the UI thread."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class MusicBrainzWorker(CancellableWorker):
    """Run one zero-arg callable and report its result, error, progress or status."""

    # `_call` may be reassigned before start() when it must reference this worker's own
    # progress.emit / status.emit as callbacks (see MusicBrainzImportDialog).

    finished = Signal(object)  # whatever the callable returned
    error = Signal(str)
    progress = Signal(int, int)  # (current, total), only from calls that report it
    status = Signal(str)  # short description of the step in flight

    def __init__(self, call: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._call = call

    def run(self):
        """Run the call and emit finished or error, unless cancelled."""
        try:
            result = self._call()
        except Exception as e:
            # Deliberately broad: the callable is arbitrary, and an exception must not
            # kill the thread silently.
            logger.error(f"MusicBrainzWorker call failed: {e}", exc_info=True)
            if not self.is_cancelled:
                self.error.emit(str(e))
            return
        finally:
            # `_call` often reads the DB (controller.get.*) before it decides to write.
            self._release_db_session()
        if not self.is_cancelled:
            self.finished.emit(result)
