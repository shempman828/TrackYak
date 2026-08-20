"""
lyrics_stats_worker.py

LyricsStatsWorker: runs LyricsStats.get_comprehensive_lyrics_stats() off the
GUI thread for the Lyrics tab. Tokenizing every track's lyrics is the
heaviest per-tab computation after the recursive places/credits rollups, so
-- like the others -- it's lazy-loaded once per dialog session rather than
blocking dialog open.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class LyricsStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, lyrics_stats, parent=None):
        super().__init__(parent)
        self.lyrics_stats = lyrics_stats

    def run(self):
        try:
            stats = self.lyrics_stats.get_comprehensive_lyrics_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing lyrics statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
