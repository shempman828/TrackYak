"""
chart_search_tab.py

Search tab: title/artist search across all weeks/years for one or both
charts. Given ~975K rows at full scale, a leading-wildcard LIKE can't use
idx_chart_entries_raw_title (SQLite can't use a B-tree index for a leading
wildcard) and would be a genuine scan, so this queries the chart_entries_fts
FTS5 virtual table (see MusicDatabase._ensure_chart_entries_fts) instead --
results are still debounced and capped, following the plan's explicit
call-out of this as the one place search must be bounded rather than
unlimited.
"""


from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
from sqlalchemy import bindparam, select, text

from src.charts.chart_entry_table import ChartEntryTable
from src.charts.chart_manual_match_actions import (
    handle_clear_match_requested,
    handle_manual_match_requested,
)
from src.charts.fts_query import build_and_query
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
        self.table.manual_match_requested.connect(self._on_manual_match_requested)
        self.table.clear_match_requested.connect(self._on_clear_match_requested)
        layout.addWidget(self.table)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._run_search)

    def set_charts(self, charts: list) -> None:
        # main_window's revisit-refresh re-calls this every time the user
        # navigates back to ChartsView; preserve the current chart selection
        # by display text so the combo doesn't snap back to "Both".
        self._charts = [(c.chart_key, c.chart_id, c.chart_name) for c in charts]
        prev = self.chart_combo.currentText()
        self.chart_combo.blockSignals(True)
        self.chart_combo.clear()
        self.chart_combo.addItem("Both")
        for _, _, name in self._charts:
            self.chart_combo.addItem(name)
        restored = self.chart_combo.findText(prev)
        self.chart_combo.setCurrentIndex(restored if restored >= 0 else 0)
        self.chart_combo.blockSignals(False)
        self._run_search()

    def _on_search_changed(self, _text: str):
        self._debounce_timer.start()

    def _selected_chart_ids(self) -> list | None:
        idx = self.chart_combo.currentIndex()
        if idx <= 0:  # "Both"
            return None
        return [self._charts[idx - 1][1]]

    def _run_search(self):
        search_text = self.search_box.text().strip()
        if not search_text:
            self.table.populate([])
            self.result_label.setText("")
            return

        match_query = build_and_query(search_text)
        if not match_query:
            self.table.populate([])
            self.result_label.setText("")
            return

        conditions = ["chart_entries_fts MATCH :match_query"]
        params = {"match_query": match_query, "result_limit": _RESULT_LIMIT + 1}
        bind_params = [bindparam("match_query"), bindparam("result_limit")]

        chart_ids = self._selected_chart_ids()
        if chart_ids:
            conditions.append("chart_entries.chart_id IN :chart_ids")
            params["chart_ids"] = tuple(chart_ids)
            bind_params.append(bindparam("chart_ids", expanding=True))

        choice = self.match_filter.currentText()
        if choice == "Matched Only":
            conditions.append("chart_entries.entity_id IS NOT NULL")
        elif choice == "Unmatched Only":
            conditions.append("chart_entries.entity_id IS NULL")

        sql = text(
            "SELECT chart_entries.* FROM chart_entries "
            "JOIN chart_entries_fts ON chart_entries_fts.rowid = chart_entries.chart_entry_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY chart_entries.chart_week DESC "
            "LIMIT :result_limit"
        ).bindparams(*bind_params)

        session = self.controller.get.session
        stmt = select(ChartEntry).from_statement(sql)
        results = session.scalars(stmt, params).all()

        truncated = len(results) > _RESULT_LIMIT
        results = results[:_RESULT_LIMIT]
        self.table.populate(results)

        if truncated:
            self.result_label.setText(f"Showing first {_RESULT_LIMIT} matches — refine your search")
        else:
            self.result_label.setText(f"{len(results)} match(es)")

    def refresh(self):
        self._run_search()

    # -----------------------------------------------------------------------
    # Manual match / clear match (ChartEntryTable context menu)
    # -----------------------------------------------------------------------

    def _on_manual_match_requested(self, chart_entry_id: int) -> None:
        handle_manual_match_requested(self, self.controller, chart_entry_id, self.refresh)

    def _on_clear_match_requested(self, chart_entry_id: int) -> None:
        handle_clear_match_requested(self, self.controller, chart_entry_id, self.refresh)
