"""
artist_stats_worker.py

ArtistStatsWorker: runs ArtistStats.get_comprehensive_artist_stats() off the
GUI thread for the Artists tab. Separate from load_data() since it's several
joined GROUP BY queries across demographics (generation/type/religion/
gender), not a single cheap aggregate.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class ArtistStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, artist_stats, parent=None):
        super().__init__(parent)
        self.artist_stats = artist_stats

    def run(self):
        try:
            stats = self.artist_stats.get_comprehensive_artist_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing artist statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
