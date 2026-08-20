"""
genre_mood_stats_worker.py

GenreMoodStatsWorker: runs GenreMoodStats.get_comprehensive_genre_mood_stats()
off the GUI thread for the Genres & Moods tab. Separate from load_data()
because the outlier-controlled mood ratings and most-niche-genre lookup walk
each mood/genre's track relationship individually rather than a single
aggregate query.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class GenreMoodStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, genre_mood_stats, parent=None):
        super().__init__(parent)
        self.genre_mood_stats = genre_mood_stats

    def run(self):
        try:
            stats = self.genre_mood_stats.get_comprehensive_genre_mood_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing genre/mood statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
