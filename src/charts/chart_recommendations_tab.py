"""
chart_recommendations_tab.py

Recommendations tab: two rankings over currently-unmatched ChartEntry rows
-- "Missing Popular" (pure chart performance) and "Gap Fills" (songs/albums
that would connect two runs of chart positions you already own in the same
week, see chart_recommendations.get_missing_gap_fills).

Both rankings scan every ChartEntry row of the selected chart(s) -- ~1M for
"All Charts" -- so they run on a ChartRecommendationsWorker background
thread, not inline in the Qt slot: computing them synchronously froze the
UI for seconds on every reload (sub-tab switch, chart-filter change,
min-gap change, and the refresh() after a bulk manual match). Only one
worker runs at a time; a reload requested while one is in flight is
coalesced (_reload_pending) and re-run once it finishes, so spinbox/combo
spam converges to the latest state without stacking threads.

The first compute is deferred until the tab is actually shown (showEvent) --
set_charts()/refresh() while it's an unvisited background tab only stash
state -- so opening ChartsView doesn't pay for a ranking the user may never
look at.

Manual matching (docs/specs/chart_recommendations_manual_match.md): both
sub-tables' bulk_match_requested signal wires to the shared
handle_bulk_manual_match_requested handler, then self.refresh() -- same
"table stays controller-free, host tab does the DB call" split as
ChartWeekBrowserTab/ChartSearchTab.
"""


from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.charts.chart_manual_match_actions import handle_bulk_manual_match_requested
from src.charts.chart_recommendation_table import ChartRecommendationTable
from src.charts.chart_recommendations_worker import (
    MODE_GAP_FILLS,
    MODE_POPULAR,
    ChartRecommendationsWorker,
)
from src.core.logger_config import logger

_RESULT_LIMIT = 100
_GAP_TAB_INDEX = 1


class ChartRecommendationsTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)]
        self._worker: ChartRecommendationsWorker | None = None
        self._reload_pending = False
        self._ever_shown = False
        self.init_ui()

    def showEvent(self, event):
        # Defer the first (expensive) compute until the tab is actually
        # opened -- see module docstring.
        super().showEvent(event)
        if not self._ever_shown:
            self._ever_shown = True
            self._reload()

    def init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItem("All Charts")
        self.chart_combo.currentIndexChanged.connect(self._reload)
        controls.addWidget(self.chart_combo)

        self.min_gap_label = QLabel("Min run length:")
        controls.addWidget(self.min_gap_label)
        self.min_gap_spin = QSpinBox()
        self.min_gap_spin.setRange(1, 200)
        self.min_gap_spin.setValue(4)
        self.min_gap_spin.setToolTip(
            "Only show gap-fill candidates that would connect at least this many "
            "already-owned chart positions (before + after combined)."
        )
        self.min_gap_spin.valueChanged.connect(self._reload)
        controls.addWidget(self.min_gap_spin)
        controls.addStretch()
        self.status_label = QLabel("")
        controls.addWidget(self.status_label)
        layout.addLayout(controls)

        self.sub_tabs = QTabWidget()
        self.popular_table = ChartRecommendationTable()
        self.gap_table = ChartRecommendationTable()
        self.sub_tabs.addTab(self.popular_table, "Missing Popular")
        self.sub_tabs.addTab(self.gap_table, "Gap Fills")
        self.sub_tabs.currentChanged.connect(self._on_sub_tab_changed)
        layout.addWidget(self.sub_tabs)

        self.popular_table.bulk_match_requested.connect(self._on_bulk_match_requested)
        self.gap_table.bulk_match_requested.connect(self._on_bulk_match_requested)

        self._on_sub_tab_changed(self.sub_tabs.currentIndex())

    def _on_bulk_match_requested(self, item):
        handle_bulk_manual_match_requested(self, self.controller, item, self.refresh)

    def _on_sub_tab_changed(self, index: int):
        is_gap_tab = index == _GAP_TAB_INDEX
        self.min_gap_label.setVisible(is_gap_tab)
        self.min_gap_spin.setVisible(is_gap_tab)
        self._reload()

    def set_charts(self, charts: list) -> None:
        self._charts = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
        self.chart_combo.blockSignals(True)
        self.chart_combo.clear()
        self.chart_combo.addItem("All Charts")
        for _, _, name in self._charts:
            self.chart_combo.addItem(name)
        self.chart_combo.blockSignals(False)
        self._reload()

    def _selected_chart_ids(self) -> list | None:
        idx = self.chart_combo.currentIndex()
        if idx <= 0:  # "All Charts"
            return None
        return [self._charts[idx - 1][1]]

    def _reload(self):
        # Nothing to show until the tab has been opened at least once; the
        # showEvent handler kicks off the first compute.
        if not self._ever_shown:
            return

        # One worker at a time: a reload requested mid-compute is coalesced
        # and re-run from _finish_reload(), so it always ends on the latest
        # chart filter / min-gap / sub-tab.
        if self._worker is not None and self._worker.isRunning():
            self._reload_pending = True
            return

        mode = MODE_GAP_FILLS if self.sub_tabs.currentIndex() == _GAP_TAB_INDEX else MODE_POPULAR
        self.status_label.setText("Computing recommendations…")
        self._worker = ChartRecommendationsWorker(
            self.controller,
            mode,
            self._selected_chart_ids(),
            self.min_gap_spin.value(),
            _RESULT_LIMIT,
        )
        self._worker.finished.connect(self._on_recs_ready)
        self._worker.error.connect(self._on_recs_error)
        self._worker.start()

    def _on_recs_ready(self, mode: str, items: list) -> None:
        table = self.gap_table if mode == MODE_GAP_FILLS else self.popular_table
        table.populate(items)
        self._finish_reload()

    def _on_recs_error(self, message: str) -> None:
        # Unlike the worker-thread pipeline in charts_view.py, nothing else
        # surfaces this -- report it here so a failed compute isn't silent.
        logger.error(f"Chart recommendations calculation failed: {message}")
        QMessageBox.warning(
            self, "Recommendations", f"Failed to compute recommendations: {message}"
        )
        self._finish_reload()

    def _finish_reload(self) -> None:
        self.status_label.setText("")
        worker = self._worker
        self._worker = None
        if worker is not None:
            # finished/error fire at the tail of run(); wait() just lets the
            # QThread finish unwinding before it's dropped (see
            # ChartsView._on_match_finished for the same pattern).
            worker.wait()
            worker.deleteLater()
        if self._reload_pending:
            self._reload_pending = False
            self._reload()

    def refresh(self):
        """Re-run the current query (e.g. after a Fetch Updates/Match Now)."""
        self._reload()
