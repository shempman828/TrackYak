"""
chart_recommendations_worker.py

Runs chart_recommendations.get_missing_popular / get_missing_gap_fills off
the UI thread, following the CancellableWorker pattern (see
src/charts/chart_matching_worker.py).

Both rankings scan every ChartEntry row of the selected chart(s) -- ~1M
rows for "All Charts" -- so computing them inline in ChartRecommendationsTab's
Qt slots froze the UI for seconds on every reload (sub-tab switch,
chart-filter change, min-gap change, and the refresh() after a bulk manual
match). The queries themselves aren't cancellation-aware; `is_cancelled` is
only checked before emitting `finished`, so a result the tab has already
superseded is dropped rather than painted.
"""

from PySide6.QtCore import Signal

from src.charts.chart_recommendations import get_missing_gap_fills, get_missing_popular
from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger

MODE_POPULAR = "popular"
MODE_GAP_FILLS = "gap_fills"


class ChartRecommendationsWorker(CancellableWorker):
    """
    Signals:
        finished(mode, list[MissingChartItem])
        error(message)

    `mode` is echoed back so ChartRecommendationsTab knows which sub-table a
    result belongs to without re-reading the (possibly since-changed) UI.
    """

    finished = Signal(str, object)
    error = Signal(str)

    def __init__(self, controller, mode, chart_ids, min_gap, limit, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mode = mode
        self.chart_ids = chart_ids
        self.min_gap = min_gap
        self.limit = limit

    def run(self):
        try:
            session = self.controller.get.session
            if self.mode == MODE_GAP_FILLS:
                items = get_missing_gap_fills(
                    session, chart_ids=self.chart_ids, min_gap=self.min_gap, limit=self.limit
                )
            else:
                items = get_missing_popular(session, chart_ids=self.chart_ids, limit=self.limit)
            if not self.is_cancelled:
                self.finished.emit(self.mode, items)
        except Exception as e:
            logger.error(f"ChartRecommendationsWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
