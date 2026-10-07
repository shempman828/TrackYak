"""Backfill Track.is_explicit from lyrics for every track where it is still NULL."""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.censor import text_contains_explicit_words
from src.foundation.logger_config import logger

# Rows per write batch; progress is emitted (and cancel checked) once per batch.
PROGRESS_INTERVAL = 25


class ExplicitRecalcWorker(CancellableWorker):
    """Set is_explicit for NULL tracks with lyrics; emits progress(done, total), finished(scanned, flagged), error(msg)."""

    progress = Signal(int, int)
    finished = Signal(int, int)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        """Scan, write in batches, and report how many rows were written and flagged."""
        try:
            # Only NULL rows are queried, so a manual or earlier value is never overwritten.
            tracks = self.controller.get.query_entities("Track", lyrics__notnull=True, is_explicit__isnull=True)
            # query_entities can't express "lyrics != ''" here -- drop blank lyrics in Python.
            candidates = [t for t in tracks if t.lyrics and t.lyrics.strip()]
            total = len(candidates)
            done = 0
            scanned = 0
            flagged = 0

            for start in range(0, total, PROGRESS_INTERVAL):
                if self.is_cancelled:
                    break
                batch = [{"track_id": t.track_id, "is_explicit": text_contains_explicit_words(t.lyrics)} for t in candidates[start : start + PROGRESS_INTERVAL]]
                _written, failed = self.controller.update.update_entities_bulk_with_fallback("Track", batch)
                failed_ids = {row["track_id"] for row in failed}
                # Count only rows that were actually written.
                for row in batch:
                    if row["track_id"] in failed_ids:
                        continue
                    scanned += 1
                    flagged += bool(row["is_explicit"])
                if failed:
                    logger.warning("ExplicitRecalcWorker: %d track(s) could not be updated", len(failed))
                done += len(batch)
                self.progress.emit(done, total)

            self.finished.emit(scanned, flagged)
        except Exception as e:
            logger.error("ExplicitRecalcWorker failed: %s", e, exc_info=True)
            self.error.emit(str(e) or type(e).__name__)
        finally:
            self._release_db_session()
