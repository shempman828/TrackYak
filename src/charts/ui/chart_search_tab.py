"""Search tab: debounced, capped full-text title/artist search across every chart week."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
from sqlalchemy import bindparam, select, text

from src.charts.fts_query import build_and_query
from src.charts.ui.chart_entry_table import ChartEntryTable
from src.charts.ui.chart_filter_toggle import ALL_CHARTS_LABEL, MatchFilterToggle, repopulate_chart_combo
from src.charts.ui.chart_manual_match_actions import handle_clear_match_requested, handle_manual_match_requested
from src.db.db_tables.chart import ChartEntry

_RESULT_LIMIT = 500
_DEBOUNCE_MS = 250


class ChartSearchTab(QWidget):
    """Full-text search over ChartEntry titles and performers."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._charts = []  # [(chart_key, chart_id, chart_name)]
        self.init_ui()

    def init_ui(self):
        """Build the search controls and the results table."""
        layout = QVBoxLayout(self)

        controls_frame = QFrame()
        controls_frame.setProperty("class", "toolbar")
        controls = QHBoxLayout(controls_frame)
        controls.setContentsMargins(10, 8, 10, 8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title or artist across all charts/years...")
        self.search_box.textChanged.connect(self._on_search_changed)
        controls.addWidget(self.search_box, stretch=3)

        controls.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItem(ALL_CHARTS_LABEL)
        self.chart_combo.currentIndexChanged.connect(self._run_search)
        controls.addWidget(self.chart_combo)

        controls.addWidget(QLabel("Show:"))
        self.match_filter = MatchFilterToggle()
        self.match_filter.currentIndexChanged.connect(self._run_search)
        controls.addWidget(self.match_filter)
        layout.addWidget(controls_frame)

        self.result_label = QLabel("")
        self.result_label.setProperty("textRole", "muted")
        layout.addWidget(self.result_label)

        # Results span charts and weeks, so show which chart and week each row is from.
        self.table = ChartEntryTable(empty_text="No results yet — try a different search.", show_context=True)
        self.table.manual_match_requested.connect(self._on_manual_match_requested)
        self.table.clear_match_requested.connect(self._on_clear_match_requested)
        layout.addWidget(self.table)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._run_search)

    def set_charts(self, charts: list) -> None:
        """Load the synced charts into the chart combo and re-run the search."""
        self._charts = repopulate_chart_combo(self.chart_combo, charts)
        self._run_search()

    def _on_search_changed(self, _text: str):
        """Restart the debounce timer on each keystroke."""
        self._debounce_timer.start()

    def _selected_chart_ids(self) -> list | None:
        """Return the selected chart's id in a list, or None for all charts."""
        idx = self.chart_combo.currentIndex()
        if idx <= 0:
            return None
        return [self._charts[idx - 1][1]]

    def _run_search(self):
        """Run the FTS query for the current text and filters and show the results."""
        match_query = build_and_query(self.search_box.text().strip())
        if not match_query:
            self.table.populate([])
            self.result_label.setText("")
            return

        # FTS5, not LIKE: a leading-wildcard LIKE can't use the title index over ~975k rows.
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

        results = self.controller.get.session.scalars(select(ChartEntry).from_statement(sql), params).all()

        # One extra row was fetched only to detect truncation.
        truncated = len(results) > _RESULT_LIMIT
        results = results[:_RESULT_LIMIT]
        self.table.populate(results)

        if truncated:
            self.result_label.setText(f"Showing first {_RESULT_LIMIT} matches — refine your search")
        else:
            self.result_label.setText(f"{len(results)} match(es)")

    def refresh(self):
        """Re-run the current search."""
        self._run_search()

    def _on_manual_match_requested(self, chart_entry_id: int) -> None:
        """Open the manual-match dialog for one entry."""
        handle_manual_match_requested(self, self.controller, chart_entry_id, self.refresh)

    def _on_clear_match_requested(self, chart_entry_id: int) -> None:
        """Clear one entry's match after confirmation."""
        handle_clear_match_requested(self, self.controller, chart_entry_id, self.refresh)
