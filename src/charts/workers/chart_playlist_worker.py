"""
chart_playlist_worker.py

Runs ChartPlaylistBuilder.generate_or_update() off the UI thread, following
the CancellableWorker pattern used by ChartMatchingWorker.
"""

from PySide6.QtCore import Signal

from src.charts.chart_playlist_builder import ChartPlaylistBuilder, ChartPlaylistStats
from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class ChartPlaylistWorker(CancellableWorker):
    """
    Signals:
        progress(years_done, years_total)
        finished(ChartPlaylistStats)
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            builder = ChartPlaylistBuilder(self.controller)
            stats: ChartPlaylistStats = builder.generate_or_update(
                progress_callback=self.progress.emit
            )

            if not self.is_cancelled:
                self.finished.emit(stats)
        except Exception as e:
            logger.error(f"ChartPlaylistWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
