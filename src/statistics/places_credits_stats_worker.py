"""
places_credits_stats_worker.py

PlacesCreditsStatsWorker: runs
PlacesCreditsStats.get_comprehensive_places_credits_stats() off the GUI
thread for the Places & Credits tab. The recursive country/artist-by-country
rollups and the per-role leaderboards (looping every role) make this the
heaviest of the per-tab workers, so -- like the others -- it's lazy-loaded
once per dialog session rather than blocking dialog open.
"""

from PySide6.QtCore import Signal
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class PlacesCreditsStatsWorker(CancellableWorker):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, places_credits_stats, parent=None):
        super().__init__(parent)
        self.places_credits_stats = places_credits_stats

    def run(self):
        try:
            stats = self.places_credits_stats.get_comprehensive_places_credits_stats()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error computing places/credits statistics: {e}")
            self.error.emit(str(e))
        finally:
            self._release_db_session()
