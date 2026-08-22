"""
mood_tag_worker.py

MoodAutoTagWorker: library-wide backfill for lyrics-based mood/place
tagging. Scans every track with non-empty lyrics and writes any
newly-matching Mood/Place associations via mood_autotag.auto_tag_track(),
the same write path the per-track auto-fill in track_edit_lyrics.py uses.
Additive only -- never removes or overwrites an existing association.
See docs/specs/lyrics_mood_tagging.md.
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger
from src.lyrics.mood_autotag import auto_tag_track, build_autotag_context

# How often to emit progress while scanning.
PROGRESS_INTERVAL = 25


class MoodAutoTagWorker(CancellableWorker):
    """
    Signals:
        progress(done, total)
        finished(scanned, mood_tags_added, place_tags_added)
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(int, int, int)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            context = build_autotag_context(self.controller)

            tracks = self.controller.get.query_entities(
                "Track", lyrics__notnull=True
            )
            candidates = [t for t in tracks if t.lyrics and t.lyrics.strip()]
            total = len(candidates)
            scanned = 0
            mood_tags_added = 0
            place_tags_added = 0

            for track in candidates:
                if self.is_cancelled:
                    break

                moods_added, places_added = auto_tag_track(
                    self.controller, track.track_id, track.lyrics, context
                )
                mood_tags_added += len(moods_added)
                place_tags_added += len(places_added)

                scanned += 1
                if scanned % PROGRESS_INTERVAL == 0:
                    self.progress.emit(scanned, total)

            self.progress.emit(scanned, total)
            self.finished.emit(scanned, mood_tags_added, place_tags_added)
        except Exception as e:
            logger.error(f"MoodAutoTagWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
