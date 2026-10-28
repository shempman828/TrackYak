"""Week Browser tab: pick a chart, year, month and week, and see that week's entries."""

import calendar

from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from sqlalchemy import select

from src.charts.ui.chart_entry_table import ChartEntryTable
from src.charts.ui.chart_filter_toggle import MatchFilterToggle, repopulate_chart_combo
from src.charts.ui.chart_manual_match_actions import handle_clear_match_requested, handle_manual_match_requested
from src.db.db_tables.chart import ChartEntry


class ChartWeekBrowserTab(QWidget):
    """Browse one chart week at a time; the small result set is queried on the UI thread."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)], populated by set_charts()
        self._weeks = []  # distinct chart_week dates for the current chart and match filter
        self.init_ui()

    def init_ui(self):
        """Build the chart/year/month/week controls and the entries table."""
        layout = QVBoxLayout(self)

        controls_frame = QFrame()
        controls_frame.setProperty("class", "toolbar")
        controls = QHBoxLayout(controls_frame)
        controls.setContentsMargins(10, 8, 10, 8)
        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.currentIndexChanged.connect(self._reload_weeks)
        controls.addWidget(self.chart_combo)

        controls.addWidget(QLabel("Year:"))
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        controls.addWidget(self.year_combo)

        controls.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox()
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        controls.addWidget(self.month_combo)

        controls.addWidget(QLabel("Week:"))
        self.week_combo = QComboBox()
        self.week_combo.currentIndexChanged.connect(self._reload_entries)
        controls.addWidget(self.week_combo)

        controls.addWidget(QLabel("Show:"))
        self.match_filter = MatchFilterToggle()
        self.match_filter.currentIndexChanged.connect(self._reload_weeks)
        controls.addWidget(self.match_filter)
        controls.addStretch()
        layout.addWidget(controls_frame)

        self.table = ChartEntryTable(empty_text="No entries for this selection.")
        self.table.manual_match_requested.connect(self._on_manual_match_requested)
        self.table.clear_match_requested.connect(self._on_clear_match_requested)
        layout.addWidget(self.table)

    def set_charts(self, charts: list) -> None:
        """Load the synced Chart rows into the chart combo and reload the weeks."""
        self._charts = repopulate_chart_combo(self.chart_combo, charts, lead_label=None)
        self._reload_weeks()

    def _current_chart_id(self) -> int | None:
        """Return the selected chart's id, or None."""
        idx = self.chart_combo.currentIndex()
        if idx < 0 or idx >= len(self._charts):
            return None
        return self._charts[idx][1]

    def _query_weeks(self, chart_id: int | None) -> list:
        """Return chart_id's distinct chart weeks, newest first, that have rows for the match filter."""
        if chart_id is None:
            return []

        query = select(ChartEntry.chart_week).where(ChartEntry.chart_id == chart_id)
        choice = self.match_filter.currentText()
        if choice == "Matched Only":
            query = query.where(ChartEntry.entity_id.isnot(None))
        elif choice == "Unmatched Only":
            query = query.where(ChartEntry.entity_id.is_(None))
        query = query.distinct().order_by(ChartEntry.chart_week.desc())
        return self.controller.get.session.scalars(query).all()

    def _reload_weeks(self, *_args):
        """Re-query the weeks and rebuild the combos, keeping the selected week where it still exists."""
        keep = self.week_combo.currentData()
        self._weeks = self._query_weeks(self._current_chart_id())
        self._fill_years(keep)

    @staticmethod
    def _set_combo(combo: QComboBox, items: list, preferred=None, default_index: int = 0) -> None:
        """Refill combo with (label, data) items without emitting signals, selecting preferred data if present."""
        combo.blockSignals(True)
        combo.clear()
        for label, data in items:
            combo.addItem(label, data)
        index = combo.findData(preferred) if preferred is not None else -1
        if index < 0 and items:
            index = default_index
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _fill_years(self, keep=None):
        """Fill the year combo (newest first) and cascade to the months."""
        years = sorted({w.year for w in self._weeks}, reverse=True)
        self._set_combo(self.year_combo, [(str(y), y) for y in years], keep.year if keep else None)
        self._fill_months(keep)

    def _fill_months(self, keep=None):
        """Fill the month combo for the selected year (latest month by default) and cascade to the weeks."""
        year = self.year_combo.currentData()
        months = sorted({w.month for w in self._weeks if w.year == year})
        preferred = keep.month if keep and keep.year == year else None
        self._set_combo(self.month_combo, [(calendar.month_name[m], m) for m in months], preferred, default_index=len(months) - 1)
        self._fill_weeks(keep)

    def _fill_weeks(self, keep=None):
        """Fill the week combo for the selected year and month (newest first) and reload the entries."""
        year = self.year_combo.currentData()
        month = self.month_combo.currentData()
        weeks = sorted((w for w in self._weeks if w.year == year and w.month == month), reverse=True)
        self._set_combo(self.week_combo, [(w.isoformat(), w) for w in weeks], keep)
        self._reload_entries()

    def _on_year_changed(self, *_args):
        """Cascade a user year change to the months."""
        self._fill_months()

    def _on_month_changed(self, *_args):
        """Cascade a user month change to the weeks."""
        self._fill_weeks()

    def _reload_entries(self, *_args):
        """Show the selected week's entries for the match filter, by position."""
        chart_id = self._current_chart_id()
        week = self.week_combo.currentData()
        if chart_id is None or week is None:
            self.table.populate([])
            return

        filters = {"chart_id__eq": chart_id, "chart_week__eq": week}
        choice = self.match_filter.currentText()
        if choice == "Matched Only":
            filters["entity_id__isnull"] = False
        elif choice == "Unmatched Only":
            filters["entity_id__isnull"] = True

        entries = self.controller.get.query_entities("ChartEntry", **filters)
        self.table.populate(sorted(entries, key=lambda e: e.position))

    def refresh(self):
        """Re-run the current query (e.g. after a Fetch Updates or Match Now)."""
        self._reload_entries()

    def _on_manual_match_requested(self, chart_entry_id: int) -> None:
        """Open the manual-match dialog for one entry."""
        handle_manual_match_requested(self, self.controller, chart_entry_id, self.refresh)

    def _on_clear_match_requested(self, chart_entry_id: int) -> None:
        """Clear one entry's match after confirmation."""
        handle_clear_match_requested(self, self.controller, chart_entry_id, self.refresh)
