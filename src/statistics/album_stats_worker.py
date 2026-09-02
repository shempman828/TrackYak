"""
album_stats_worker.py

AlbumStatsWorker: runs AlbumStats.get_comprehensive_album_stats() off the
GUI thread for the Albums tab. Separate from load_data() because the
genre-spread diversity score walks every album's tracks and their genres
individually rather than a single aggregate query.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class AlbumStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, album_stats, parent=None):
        super().__init__(parent)
        self.album_stats = album_stats

    def run(self):
        try:
            stats = self.album_stats.get_comprehensive_album_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing album statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
