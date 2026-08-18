"""
chart_week_browser_tab.py

Week Browser tab: pick a chart + a week, see that week's entries. Small
result set (~100-200 rows), so the query runs synchronously off
controller.get -- no worker needed, unlike the bulk import/matching passes.
"""

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from src.charts.chart_entry_table import ChartEntryTable
from src.db.db_tables.chart import ChartEntry

_MATCH_FILTERS = ["All", "Matched Only", "Unmatched Only"]

_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


class ChartWeekBrowserTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)], populated by set_charts()
        self._weeks = []  # all distinct ChartEntry.chart_week dates for the current chart
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.currentIndexChanged.connect(self._on_chart_changed)
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
        self.match_filter = QComboBox()
        self.match_filter.addItems(_MATCH_FILTERS)
        self.match_filter.currentIndexChanged.connect(self._on_match_filter_changed)
        controls.addWidget(self.match_filter)
        controls.addStretch()
        layout.addLayout(controls)

        self.table = ChartEntryTable()
        layout.addWidget(self.table)

    def set_charts(self, charts: list) -> None:
        """charts: list of Chart ORM rows with last_synced_week set (i.e.
        actually imported) -- called by ChartsView once data is available."""
        self._charts = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
        self.chart_combo.blockSignals(True)
        self.chart_combo.clear()
        for _, _, name in self._charts:
            self.chart_combo.addItem(name)
        self.chart_combo.blockSignals(False)
        self._on_chart_changed()

    def _current_chart_id(self) -> Optional[int]:
        idx = self.chart_combo.currentIndex()
        if idx < 0 or idx >= len(self._charts):
            return None
        return self._charts[idx][1]

    def _query_weeks(self, chart_id: Optional[int]) -> list:
        """Distinct ChartEntry.chart_week dates for chart_id, constrained to
        the currently selected match filter so the year/month/week combos
        only ever offer weeks that actually have matching results."""
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

    def _on_chart_changed(self):
        chart_id = self._current_chart_id()
        self._weeks = self._query_weeks(chart_id)
        self._rebuild_year_combo()

    def _on_match_filter_changed(self):
        chart_id = self._current_chart_id()
        self._weeks = self._query_weeks(chart_id)
        self._rebuild_year_combo()

    def _rebuild_year_combo(self):
        years = sorted({w.year for w in self._weeks}, reverse=True)
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for year in years:
            self.year_combo.addItem(str(year), year)
        self.year_combo.blockSignals(False)
        self._on_year_changed()

    def _on_year_changed(self):
        year = self.year_combo.currentData()
        months = sorted({w.month for w in self._weeks if w.year == year})
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        for month in months:
            self.month_combo.addItem(_MONTH_NAMES[month - 1], month)
        if months:
            self.month_combo.setCurrentIndex(months.index(max(months)))
        self.month_combo.blockSignals(False)
        self._on_month_changed()

    def _on_month_changed(self):
        year = self.year_combo.currentData()
        month = self.month_combo.currentData()
        weeks = []
        if year is not None and month is not None:
            weeks = sorted(
                (w for w in self._weeks if w.year == year and w.month == month),
                reverse=True,
            )
        self.week_combo.blockSignals(True)
        self.week_combo.clear()
        for week in weeks:
            self.week_combo.addItem(week.isoformat(), week)
        self.week_combo.blockSignals(False)
        self._reload_entries()

    def _reload_entries(self):
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
        entries = sorted(entries, key=lambda e: e.position)
        self.table.populate(entries)

    def refresh(self):
        """Re-run the current query (e.g. after a Fetch Updates/Match Now)."""
        self._reload_entries()
