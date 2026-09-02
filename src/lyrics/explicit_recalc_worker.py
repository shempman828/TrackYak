"""
explicit_recalc_worker.py

ExplicitRecalcWorker: backfills Track.is_explicit for every track that has
lyrics but has never had is_explicit determined (NULL), by comparing lyrics
text against the same explicit word list censor.py already uses for
display-masking. Never overwrites an existing non-NULL value -- manual or
previously auto-calculated -- same non-clobbering rule as the per-track
auto-fill in track_edit_lyrics.py's collect_changes().
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.censor import text_contains_explicit_words
from src.foundation.logger_config import logger

# How often to emit progress while scanning.
PROGRESS_INTERVAL = 25


class ExplicitRecalcWorker(CancellableWorker):
    """
    Signals:
        progress(done, total)
        finished(scanned, flagged_explicit)
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(int, int)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            tracks = self.controller.get.query_entities(
                "Track", lyrics__notnull=True, is_explicit__isnull=True
            )
            # query_entities can't express "lyrics != ''" alongside the
            # NULL check in one filter set -- drop blank/whitespace-only
            # lyrics here instead.
            candidates = [t for t in tracks if t.lyrics and t.lyrics.strip()]
            total = len(candidates)
            scanned = 0
            flagged = 0

            for track in candidates:
                if self.is_cancelled:
                    break
                is_explicit = text_contains_explicit_words(track.lyrics)
                self.controller.update.update_entity(
                    "Track", track.track_id, is_explicit=is_explicit
                )
                scanned += 1
                if is_explicit:
                    flagged += 1
                if scanned % PROGRESS_INTERVAL == 0:
                    self.progress.emit(scanned, total)

            self.progress.emit(scanned, total)
            self.finished.emit(scanned, flagged)
        except Exception as e:
            logger.error(f"ExplicitRecalcWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
