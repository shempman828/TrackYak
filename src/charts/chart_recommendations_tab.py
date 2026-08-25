"""
chart_recommendations_tab.py

Recommendations tab: two rankings over currently-unmatched ChartEntry rows
-- "Missing Popular" (pure chart performance) and "Gap Fills" (songs/albums
that would connect two runs of chart positions you already own in the same
week, see chart_recommendations.get_missing_gap_fills). Queries run
synchronously off controller.get.session, same precedent as
ChartWeekBrowserTab/ChartSearchTab -- no worker thread.

Manual matching (docs/specs/chart_recommendations_manual_match.md): both
sub-tables' bulk_match_requested signal wires to the shared
handle_bulk_manual_match_requested handler, then self.refresh() -- same
"table stays controller-free, host tab does the DB call" split as
ChartWeekBrowserTab/ChartSearchTab.
"""

from typing import Optional

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
from src.charts.chart_recommendations import get_missing_gap_fills, get_missing_popular
from src.core.logger_config import logger

_RESULT_LIMIT = 100
_GAP_TAB_INDEX = 1


class ChartRecommendationsTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)]
        self.init_ui()

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

    def _selected_chart_ids(self) -> Optional[list]:
        idx = self.chart_combo.currentIndex()
        if idx <= 0:  # "All Charts"
            return None
        return [self._charts[idx - 1][1]]

    def _reload(self):
        session = self.controller.get.session
        chart_ids = self._selected_chart_ids()
        try:
            if self.sub_tabs.currentIndex() == _GAP_TAB_INDEX:
                items = get_missing_gap_fills(
                    session,
                    chart_ids=chart_ids,
                    min_gap=self.min_gap_spin.value(),
                    limit=_RESULT_LIMIT,
                )
                self.gap_table.populate(items)
            else:
                items = get_missing_popular(session, chart_ids=chart_ids, limit=_RESULT_LIMIT)
                self.popular_table.populate(items)
        except Exception as exc:
            # Runs synchronously in a Qt slot (see module docstring) -- unlike
            # the worker-thread pipeline in charts_view.py, an uncaught
            # exception here isn't caught by any error signal and crashes the
            # whole app instead of just this tab, so it must be handled here.
            logger.error(f"Chart recommendations calculation failed: {exc}")
            QMessageBox.warning(
                self, "Recommendations", f"Failed to compute recommendations: {exc}"
            )

    def refresh(self):
        """Re-run the current query (e.g. after a Fetch Updates/Match Now)."""
        self._reload()
