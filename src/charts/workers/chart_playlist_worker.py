"""Background runner for ChartPlaylistBuilder.generate_or_update()."""

from PySide6.QtCore import Signal

from src.charts.chart_playlist_builder import ChartPlaylistBuilder, ChartPlaylistStats
from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger


class ChartPlaylistWorker(CancellableWorker):
    """Build or update every chart's playlist tree."""

    progress = Signal(int, int)  # years_done, years_total
    finished = Signal(object)  # ChartPlaylistStats
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        """Run the playlist builder."""
        try:
            builder = ChartPlaylistBuilder(self.controller)
            stats: ChartPlaylistStats = builder.generate_or_update(progress_callback=self.progress.emit)

            if not self.is_cancelled:
                self.finished.emit(stats)
        except Exception as e:
            logger.error(f"ChartPlaylistWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
