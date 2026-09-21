"""MoodAutoTagWorker: library-wide backfill for lyrics-based mood/place tagging."""

# Scans every track with non-empty lyrics and writes any newly-matching
# Mood/Place associations via mood_autotag.auto_tag_track(), the same write
# path the per-track auto-fill in track_edit_lyrics.py uses. Additive only
# -- never removes or overwrites an existing association.

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.mood.mood_autotag import auto_tag_track, build_autotag_context, flush_pending_queue

# How often to emit progress while scanning.
PROGRESS_INTERVAL = 25


class MoodAutoTagWorker(CancellableWorker):
    """
    Signals:
        progress(done, total, mood_tags_added, place_tags_added, place_tags_queued)
        finished(scanned, mood_tags_added, place_tags_added, place_tags_queued)
        error(message)
    """

    progress = Signal(int, int, int, int, int)
    finished = Signal(int, int, int, int)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            context = build_autotag_context(self.controller)

            tracks = self.controller.get.query_entities("Track", lyrics__notnull=True)
            candidates = [t for t in tracks if t.lyrics and t.lyrics.strip()]
            total = len(candidates)
            scanned = 0
            mood_tags_added = 0
            place_tags_added = 0
            place_tags_queued = 0

            for track in candidates:
                if self.is_cancelled:
                    break

                moods_added, places_added, places_queued = auto_tag_track(
                    self.controller, track.track_id, track.lyrics, context
                )
                mood_tags_added += len(moods_added)
                place_tags_added += len(places_added)
                place_tags_queued += len(places_queued)

                scanned += 1
                if scanned % PROGRESS_INTERVAL == 0:
                    self.progress.emit(
                        scanned, total, mood_tags_added, place_tags_added, place_tags_queued
                    )

            flush_pending_queue(context)
            self.progress.emit(scanned, total, mood_tags_added, place_tags_added, place_tags_queued)
            self.finished.emit(scanned, mood_tags_added, place_tags_added, place_tags_queued)
        except Exception as e:
            logger.error(f"MoodAutoTagWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
