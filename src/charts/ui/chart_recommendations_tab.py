"""Recommendations tab: "Missing Popular" and "Gap Fills" rankings, computed on a background worker."""

import datetime

from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget

from src.charts.chart_recommendations import chart_week_years
from src.charts.ui.chart_filter_toggle import ALL_CHARTS_LABEL, repopulate_chart_combo
from src.charts.ui.chart_manual_match_actions import handle_bulk_manual_match_requested
from src.charts.ui.chart_recommendation_table import ChartRecommendationTable
from src.charts.workers.chart_recommendations_worker import MODE_GAP_FILLS, MODE_POPULAR, ChartRecommendationsWorker
from src.foundation.logger_config import logger

_ALL_YEARS_LABEL = "All Years"

_RESULT_LIMIT = 100
_GAP_TAB_INDEX = 1


class ChartRecommendationsTab(QWidget):
    """Two rankings of unmatched chart items, filterable by chart, year range and minimum run length."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)]
        self._worker: ChartRecommendationsWorker | None = None
        self._reload_pending = False
        self._ever_shown = False
        # Inclusive chart_week bounds (both None == "All Years"), kept on the tab
        # so they survive the menu rebuild in set_charts().
        self._week_from: datetime.date | None = None
        self._week_to: datetime.date | None = None
        self._year_label = _ALL_YEARS_LABEL
        self._available_years: set[int] = set()
        # PySide does not keep addMenu() submenus alive; hold references.
        self._decade_menus: list[QMenu] = []
        self.init_ui()

    def showEvent(self, event):
        """Run the first (expensive) year scan and ranking only when the tab is first opened."""
        super().showEvent(event)
        if not self._ever_shown:
            self._ever_shown = True
            self._build_year_menu()
            self._restore_year_selection()
            self._reload()

    def init_ui(self):
        """Build the filter controls and the two ranking sub-tables."""
        layout = QVBoxLayout(self)

        controls_frame = QFrame()
        controls_frame.setProperty("class", "toolbar")
        controls = QHBoxLayout(controls_frame)
        controls.setContentsMargins(10, 8, 10, 8)
        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItem(ALL_CHARTS_LABEL)
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
        self.min_gap_spin.setToolTip("Only show gap-fill candidates that would connect at least this many already-owned chart positions (before + after combined).")
        self.min_gap_spin.valueChanged.connect(self._reload)
        controls.addWidget(self.min_gap_spin)
        controls.addStretch()
        self.status_label = QLabel("")
        self.status_label.setProperty("textRole", "muted")
        controls.addWidget(self.status_label)
        layout.addWidget(controls_frame)

        self.sub_tabs = QTabWidget()
        self.popular_table = ChartRecommendationTable(empty_text="No missing popular songs found.")
        self.gap_table = ChartRecommendationTable(empty_text="No gap-fill candidates found.")
        self.sub_tabs.addTab(self.popular_table, "Missing Popular")
        self.sub_tabs.addTab(self.gap_table, "Gap Fills")
        self.sub_tabs.currentChanged.connect(self._on_sub_tab_changed)
        layout.addWidget(self.sub_tabs)

        self.popular_table.bulk_match_requested.connect(self._on_bulk_match_requested)
        self.gap_table.bulk_match_requested.connect(self._on_bulk_match_requested)

        self._on_sub_tab_changed(self.sub_tabs.currentIndex())

    def _on_bulk_match_requested(self, item):
        """Open the manual-match dialog for a whole recommendation group."""
        handle_bulk_manual_match_requested(self, self.controller, item, self.refresh)

    def _on_sub_tab_changed(self, index: int):
        """Show the min-gap control only on Gap Fills, and reload."""
        is_gap_tab = index == _GAP_TAB_INDEX
        self.min_gap_label.setVisible(is_gap_tab)
        self.min_gap_spin.setVisible(is_gap_tab)
        self._reload()

    def set_charts(self, charts: list) -> None:
        """Load the synced charts, and rebuild the year menu and rankings if the tab was opened."""
        self._charts = repopulate_chart_combo(self.chart_combo, charts)
        if not self._ever_shown:
            return  # showEvent builds everything on first open
        self._build_year_menu()
        self._restore_year_selection()
        self._reload()

    def _selected_chart_ids(self) -> list | None:
        """Return the selected chart's id in a list, or None for all charts."""
        idx = self.chart_combo.currentIndex()
        if idx <= 0:
            return None
        return [self._charts[idx - 1][1]]

    def _build_year_menu(self) -> None:
        """Rebuild the "All Years" + per-decade year menu from the years that have entries (all charts)."""
        self._year_menu.clear()
        for old in self._decade_menus:
            old.deleteLater()
        self._decade_menus = []

        all_action = self._year_menu.addAction(_ALL_YEARS_LABEL)
        all_action.triggered.connect(lambda: self._select_year_bound(None, None, _ALL_YEARS_LABEL))

        # The DISTINCT-year scan covers ~1M rows, so it is skipped until the tab is opened.
        years = chart_week_years(self.controller.get.session) if self._ever_shown else []
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
            entire.triggered.connect(lambda _=False, d=decade: self._select_year_bound(datetime.date(d, 1, 1), datetime.date(d + 9, 12, 31), f"{d}s"))
            decade_menu.addSeparator()
            for year in decade_years:
                act = decade_menu.addAction(str(year))
                act.triggered.connect(lambda _=False, y=year: self._select_year_bound(datetime.date(y, 1, 1), datetime.date(y, 12, 31), str(y)))

    def _select_year_bound(self, week_from: datetime.date | None, week_to: datetime.date | None, label: str) -> None:
        """Set the chart_week bound and button label, and reload."""
        self._week_from = week_from
        self._week_to = week_to
        self._year_label = label
        self.year_button.setText(label)
        self._reload()

    def _restore_year_selection(self) -> None:
        """Re-sync the button text to the kept bound, or reset to "All Years" if that range has no data."""
        if self._week_from is not None:
            span = range(self._week_from.year, self._week_to.year + 1)
            if self._available_years.isdisjoint(span):
                self._week_from = self._week_to = None
                self._year_label = _ALL_YEARS_LABEL
        self.year_button.setText(self._year_label)

    def _reload(self, *_args):
        """Start a ranking worker for the current filters, or queue one if a worker is running."""
        if not self._ever_shown:
            return

        # One worker at a time: a mid-compute reload is coalesced and re-run from
        # _finish_reload(), so it always ends on the latest filters.
        if self._worker is not None and self._worker.isRunning():
            self._reload_pending = True
            return

        mode = MODE_GAP_FILLS if self.sub_tabs.currentIndex() == _GAP_TAB_INDEX else MODE_POPULAR
        self.status_label.setText("Computing recommendations…")
        self._worker = ChartRecommendationsWorker(self.controller, mode, self._selected_chart_ids(), self.min_gap_spin.value(), _RESULT_LIMIT, self._week_from, self._week_to)
        self._worker.finished.connect(self._on_recs_ready)
        self._worker.error.connect(self._on_recs_error)
        self._worker.start()

    def _on_recs_ready(self, mode: str, items: list) -> None:
        """Show a finished ranking in its sub-table."""
        table = self.gap_table if mode == MODE_GAP_FILLS else self.popular_table
        table.populate(items)
        self._finish_reload()

    def _on_recs_error(self, message: str) -> None:
        """Report a failed ranking, which nothing else surfaces."""
        logger.error(f"Chart recommendations calculation failed: {message}")
        QMessageBox.warning(self, "Recommendations", f"Failed to compute recommendations: {message}")
        self._finish_reload()

    def _finish_reload(self) -> None:
        """Drop the finished worker and run a queued reload, if any."""
        self.status_label.setText("")
        worker = self._worker
        self._worker = None
        if worker is not None:
            # finished/error fire at the tail of run(); let the thread unwind before dropping it.
            worker.wait()
            worker.deleteLater()
        if self._reload_pending:
            self._reload_pending = False
            self._reload()

    def refresh(self):
        """Re-run the current query (e.g. after a Fetch Updates/Match Now)."""
        self._reload()
