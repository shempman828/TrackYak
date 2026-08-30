"""
chart_matching_worker.py

Runs chart_matching.match_chart() off the UI thread, following the
CancellableWorker pattern (see src/musicbrainz/musicbrainz_worker.py).
"""

from PySide6.QtCore import Signal

from src.charts.chart_matching import MatchStats, match_chart
from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class ChartMatchingWorker(CancellableWorker):
    """
    Signals:
        stage(message)
        progress(entries_scored, entries_total, entries_matched)
        finished(MatchStats)
        error(message)
    """

    stage = Signal(str)
    progress = Signal(int, int, int)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, controller, chart_key: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.chart_key = chart_key

    def run(self):
        try:
            chart = self.controller.get.get_entity_object("Chart", chart_key=self.chart_key)
            if chart is None:
                raise ValueError(f"No Chart row registered for chart_key={self.chart_key!r}")

            stats: MatchStats = match_chart(
                self.controller.get.session,
                chart,
                progress_callback=self.progress.emit,
                stage_callback=self.stage.emit,
                is_cancelled=lambda: self.is_cancelled,
            )

            if not self.is_cancelled:
                self.finished.emit(stats)
        except Exception as e:
            logger.error(f"ChartMatchingWorker failed for {self.chart_key}: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
