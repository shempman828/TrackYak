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
min-gap change, year/decade-filter change, and the refresh() after a bulk
manual match). Only one worker runs at a time; a reload requested while one
is in flight is coalesced (_reload_pending) and re-run once it finishes, so
spinbox/combo spam converges to the latest state without stacking threads.

The year/decade filter (a QToolButton menu, "All Years" + a submenu per
populated decade) narrows both rankings to a chart_week range; its bound is
kept on the tab so the menu rebuild in set_charts() doesn't lose it.

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

import datetime

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.charts.chart_manual_match_actions import handle_bulk_manual_match_requested
from src.charts.chart_recommendation_table import ChartRecommendationTable
from src.charts.chart_recommendations import chart_week_years
from src.charts.chart_recommendations_worker import (
    MODE_GAP_FILLS,
    MODE_POPULAR,
    ChartRecommendationsWorker,
)
from src.foundation.logger_config import logger

_ALL_YEARS_LABEL = "All Years"

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
        # Year/decade filter: inclusive chart_week bounds, both None == "All
        # Years". Kept on the tab (not read back off the menu) so it
        # survives the menu rebuild in set_charts().
        self._week_from: datetime.date | None = None
        self._week_to: datetime.date | None = None
        self._year_label = _ALL_YEARS_LABEL
        self._available_years: set[int] = set()
        # PySide won't keep the submenus from addMenu() alive on its own --
        # hold a reference so they aren't GC'd out from under the menu.
        self._decade_menus: list[QMenu] = []
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

        controls.addWidget(QLabel("Years:"))
        self.year_button = QToolButton()
        self.year_button.setPopupMode(QToolButton.InstantPopup)
        self.year_button.setText(self._year_label)
        self._year_menu = QMenu(self.year_button)
        self.year_button.setMenu(self._year_menu)
        controls.addWidget(self.year_button)
        self._build_year_menu()

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
        # main_window's revisit-refresh re-calls this every time the user
        # navigates back to ChartsView; preserve the current chart selection
        # by display text so the combo doesn't snap back to "All Charts".
        self._charts = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
        prev = self.chart_combo.currentText()
        self.chart_combo.blockSignals(True)
        self.chart_combo.clear()
        self.chart_combo.addItem("All Charts")
        for _, _, name in self._charts:
            self.chart_combo.addItem(name)
        restored = self.chart_combo.findText(prev)
        self.chart_combo.setCurrentIndex(restored if restored >= 0 else 0)
        self.chart_combo.blockSignals(False)
        self._build_year_menu()
        self._restore_year_selection()
        self._reload()

    def _selected_chart_ids(self) -> list | None:
        idx = self.chart_combo.currentIndex()
        if idx <= 0:  # "All Charts"
            return None
        return [self._charts[idx - 1][1]]

    def _build_year_menu(self) -> None:
        """(Re)build the year/decade menu from the calendar years that
        actually have chart entries -- "All Years", then one submenu per
        decade with data, each holding "Entire decade" + its populated
        years. Not scoped to the chart combo (see spec)."""
        self._year_menu.clear()
        for old in self._decade_menus:
            old.deleteLater()
        self._decade_menus = []

        all_action = self._year_menu.addAction(_ALL_YEARS_LABEL)
        all_action.triggered.connect(lambda: self._select_year_bound(None, None, _ALL_YEARS_LABEL))

        years = chart_week_years(self.controller.get.session)
        self._available_years = set(years)
        if not years:
            return

        self._year_menu.addSeparator()
        first_decade = years[0] - years[0] % 10
        last_decade = years[-1] - years[-1] % 10
        for decade in range(first_decade, last_decade + 1, 10):
            decade_years = [y for y in range(decade, decade + 10) if y in self._available_years]
            if not decade_years:
                continue
            decade_menu = QMenu(f"{decade}s", self._year_menu)
            self._decade_menus.append(decade_menu)
            self._year_menu.addMenu(decade_menu)
            entire = decade_menu.addAction("Entire decade")
            entire.triggered.connect(
                lambda _=False, d=decade: self._select_year_bound(
                    datetime.date(d, 1, 1), datetime.date(d + 9, 12, 31), f"{d}s"
                )
            )
            decade_menu.addSeparator()
            for year in decade_years:
                act = decade_menu.addAction(str(year))
                act.triggered.connect(
                    lambda _=False, y=year: self._select_year_bound(
                        datetime.date(y, 1, 1), datetime.date(y, 12, 31), str(y)
                    )
                )

    def _select_year_bound(
        self, week_from: datetime.date | None, week_to: datetime.date | None, label: str
    ) -> None:
        self._week_from = week_from
        self._week_to = week_to
        self._year_label = label
        self.year_button.setText(label)
        self._reload()

    def _restore_year_selection(self) -> None:
        """Rebuilding the menu drops its checked state, but the bound lives
        on the tab -- just re-sync the button text, falling back to "All
        Years" if the selected year/decade no longer has any chart data."""
        if self._week_from is not None:
            span = range(self._week_from.year, self._week_to.year + 1)
            if self._available_years.isdisjoint(span):
                self._week_from = self._week_to = None
                self._year_label = _ALL_YEARS_LABEL
        self.year_button.setText(self._year_label)

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
            self._week_from,
            self._week_to,
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
