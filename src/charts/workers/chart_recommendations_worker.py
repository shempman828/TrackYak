"""Background runner for the chart recommendation rankings."""

from PySide6.QtCore import Signal

from src.charts.chart_recommendations import get_missing_gap_fills, get_missing_popular
from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger

MODE_POPULAR = "popular"
MODE_GAP_FILLS = "gap_fills"


class ChartRecommendationsWorker(CancellableWorker):
    """Compute one recommendation ranking off the UI thread."""

    # mode is echoed back so the tab knows which sub-table the result is for.
    finished = Signal(str, object)  # mode, list[MissingChartItem]
    error = Signal(str)

    def __init__(self, controller, mode, chart_ids, min_gap, limit, week_from=None, week_to=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mode = mode
        self.chart_ids = chart_ids
        self.min_gap = min_gap
        self.limit = limit
        self.week_from = week_from
        self.week_to = week_to

    def run(self):
        """Run the ranking for this worker's mode."""
        try:
            session = self.controller.get.session
            if self.mode == MODE_GAP_FILLS:
                items = get_missing_gap_fills(session, chart_ids=self.chart_ids, min_gap=self.min_gap, limit=self.limit, week_from=self.week_from, week_to=self.week_to)
            else:
                items = get_missing_popular(session, chart_ids=self.chart_ids, limit=self.limit, week_from=self.week_from, week_to=self.week_to)
            # The queries can't be interrupted; a superseded result is just dropped.
            if not self.is_cancelled:
                self.finished.emit(self.mode, items)
        except Exception as e:
            logger.error(f"ChartRecommendationsWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
