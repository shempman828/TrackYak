"""
musicbrainz_worker.py

Runs a single MusicBrainz client call (search or a follow-up "complete"
lookup) off the UI thread, matching the CancellableWorker pattern already
used for artist fuzzy-matching, art caching, etc.
"""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class MusicBrainzWorker(CancellableWorker):
    """
    Generic background runner for one MusicBrainz client call.

    Wraps a zero-arg callable (a search_* or complete_*_enrichment function
    pre-bound with its arguments via functools.partial or a lambda) so the
    same worker class serves every entity type and both the search step and
    the post-selection enrichment step.

    Signals:
        finished(result) - whatever the callable returned
        error(message)
        progress(current, total) - emitted by calls that accept a
            `progress_callback` kwarg (e.g. fetch_release_detail resolving
            recording-location area chains); not every call reports this.

    `call` may be a placeholder (e.g. `lambda: None`) at construction time
    and reassigned via `worker._call = ...` before `start()` if it needs to
    reference `worker.progress.emit` as its own progress_callback.
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self, call: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._call = call

    def run(self):
        try:
            result = self._call()
        except Exception as e:
            # Intentional broad boundary catch: this is a QThread's run()
            # body wrapping an arbitrary zero-arg callable (see class
            # docstring — not guaranteed to be a musicbrainz_client function
            # that only raises MusicBrainzLookupError) and must not let an
            # exception kill the thread silently — report it to the UI
            # via the error signal instead.
            logger.error(f"MusicBrainzWorker call failed: {e}", exc_info=True)
            self.error.emit(str(e))
            return
        finally:
            # `_call` frequently includes a controller.get.* lookup (e.g.
            # checking for an existing local match) before deciding whether
            # to write anything -- see CancellableWorker's docstring.
            self._release_db_session()
        if not self.is_cancelled:
            self.finished.emit(result)
