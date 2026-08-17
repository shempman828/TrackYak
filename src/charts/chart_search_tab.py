"""
chart_search_tab.py

Search tab: title/artist search across all weeks/years for one or both
charts. Given ~975K rows at full scale, a leading-wildcard LIKE can't use
idx_chart_entries_raw_title (SQLite can't use a B-tree index for a leading
wildcard) and is a genuine scan -- so results are debounced and capped,
following the plan's explicit call-out of this as the one place search
must be bounded rather than unlimited.
"""

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import or_, select

from src.charts.chart_entry_table import ChartEntryTable
from src.db.db_tables.chart import ChartEntry

_MATCH_FILTERS = ["All", "Matched Only", "Unmatched Only"]
_RESULT_LIMIT = 500
_DEBOUNCE_MS = 250


class ChartSearchTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)]
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title or artist across all charts/years...")
        self.search_box.textChanged.connect(self._on_search_changed)
        controls.addWidget(self.search_box, stretch=3)

        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItem("Both")
        self.chart_combo.currentIndexChanged.connect(self._run_search)
        controls.addWidget(self.chart_combo)

        controls.addWidget(QLabel("Show:"))
        self.match_filter = QComboBox()
        self.match_filter.addItems(_MATCH_FILTERS)
        self.match_filter.currentIndexChanged.connect(self._run_search)
        controls.addWidget(self.match_filter)
        layout.addLayout(controls)

        self.result_label = QLabel("")
        layout.addWidget(self.result_label)

        self.table = ChartEntryTable()
        layout.addWidget(self.table)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._run_search)

    def set_charts(self, charts: list) -> None:
        self._charts = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
        self.chart_combo.blockSignals(True)
        self.chart_combo.clear()
        self.chart_combo.addItem("Both")
        for _, _, name in self._charts:
            self.chart_combo.addItem(name)
        self.chart_combo.blockSignals(False)
        self._run_search()

    def _on_search_changed(self, _text: str):
        self._debounce_timer.start()

    def _selected_chart_ids(self) -> Optional[list]:
        idx = self.chart_combo.currentIndex()
        if idx <= 0:  # "Both"
            return None
        return [self._charts[idx - 1][1]]

    def _run_search(self):
        text = self.search_box.text().strip()
        if not text:
            self.table.populate([])
            self.result_label.setText("")
            return

        session = self.controller.get.session
        stmt = select(ChartEntry).where(
            or_(
                ChartEntry.raw_title.ilike(f"%{text}%"),
                ChartEntry.raw_performer.ilike(f"%{text}%"),
            )
        )
        chart_ids = self._selected_chart_ids()
        if chart_ids:
            stmt = stmt.where(ChartEntry.chart_id.in_(chart_ids))

        choice = self.match_filter.currentText()
        if choice == "Matched Only":
            stmt = stmt.where(ChartEntry.entity_id.is_not(None))
        elif choice == "Unmatched Only":
            stmt = stmt.where(ChartEntry.entity_id.is_(None))

        stmt = stmt.order_by(ChartEntry.chart_week.desc()).limit(_RESULT_LIMIT + 1)
        results = session.scalars(stmt).all()

        truncated = len(results) > _RESULT_LIMIT
        results = results[:_RESULT_LIMIT]
        self.table.populate(results)

        if truncated:
            self.result_label.setText(
                f"Showing first {_RESULT_LIMIT} matches — refine your search"
            )
        else:
            self.result_label.setText(f"{len(results)} match(es)")

    def refresh(self):
        self._run_search()
