"""
chart_recommendation_table.py

Flat results table for aggregated MissingChartItem rows (see
chart_recommendations.py). Same QTreeWidget convention as
chart_entry_table.py, but there's no single ChartEntry backing a row here
-- a recommendation folds together every week the song/album appeared
unmatched -- so rows carry no chart_entry_id and there is no
selection-driven detail view.

The "Connects" column shows gap_run_length, which is only meaningful for
get_missing_gap_fills results (0/blank for get_missing_popular).

Manual matching (docs/specs/chart_recommendations_manual_match.md) is
exposed as a right-click context menu with a single "Match to X…" action --
no "Clear Match", since every row here is unmatched by construction (that's
the underlying query's entity_id IS NULL filter). Like ChartEntryTable, the
action emits a signal carrying the row's data rather than touching the DB
directly, so this widget stays controller-free.
"""

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem

from src.charts.chart_recommendations import MissingChartItem
from src.charts.chart_table_placeholder import install_empty_placeholder, sync_empty_placeholder

_COLUMNS = ["Title", "Artist", "Type", "Chart", "Peak", "Weeks on Chart", "Connects"]
_SORT_VALUE_ROLE = Qt.UserRole + 1
_NUMERIC_COLUMNS = {4, 5, 6}  # Peak, Weeks on Chart, Connects
_TITLE_WIDTH = 260
_ARTIST_WIDTH = 200


class _RecommendationTreeItem(QTreeWidgetItem):
    """Sorts the Peak, Weeks on Chart, and Connects columns by their numeric
    value instead of lexicographically (so 9 sorts before 10), matching the
    pattern in genre_tree_builder.py's _GenreTreeItem."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else 0
        if column in _NUMERIC_COLUMNS:
            return self.data(column, _SORT_VALUE_ROLE) < other.data(column, _SORT_VALUE_ROLE)
        return self.text(column).lower() < other.text(column).lower()


class ChartRecommendationTable(QTreeWidget):
    bulk_match_requested = Signal(object)  # MissingChartItem

    def __init__(self, parent=None, empty_text: str = "No recommendations found."):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(True)
        # No column sorted at start, so results keep arriving in their
        # pre-ranked order until the user clicks a header.
        self.header().setSortIndicator(-1, Qt.AscendingOrder)
        # Interactive (not Stretch) so the user can drag every column border;
        # Stretch sections ignore drags. Title/Artist start wide instead.
        self.header().setSectionResizeMode(QHeaderView.Interactive)
        self.setColumnWidth(0, _TITLE_WIDTH)
        self.setColumnWidth(1, _ARTIST_WIDTH)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._empty_label = install_empty_placeholder(self, empty_text)

    def populate(self, items: Iterable[MissingChartItem]) -> None:
        self.clear()
        for item in items:
            tree_item = _RecommendationTreeItem(
                [
                    item.raw_title,
                    item.raw_performer,
                    item.entity_type or "",
                    item.chart_name,
                    str(item.peak_position) if item.peak_position else "",
                    str(item.weeks_on_chart) if item.weeks_on_chart else "",
                    str(item.gap_run_length) if item.gap_run_length else "",
                ]
            )
            tree_item.setData(0, Qt.UserRole, item)
            tree_item.setData(4, _SORT_VALUE_ROLE, item.peak_position or 0)
            tree_item.setData(5, _SORT_VALUE_ROLE, item.weeks_on_chart or 0)
            tree_item.setData(6, _SORT_VALUE_ROLE, item.gap_run_length or 0)
            self.addTopLevelItem(tree_item)
        sync_empty_placeholder(self, self._empty_label)

    def context_menu_for_item(self, item: MissingChartItem) -> QMenu:
        """Build (but don't show) the manual-match context menu for `item`.
        Split out from _show_context_menu so tests can inspect/trigger the
        menu's action without going through QMenu.exec()'s blocking popup
        loop (mirrors ChartEntryTable.context_menu_for_entry)."""
        menu = QMenu(self)
        match_action = menu.addAction(f"Match to {item.entity_type}…")
        match_action.triggered.connect(lambda: self.bulk_match_requested.emit(item))
        return menu

    def _show_context_menu(self, pos) -> None:
        tree_item = self.itemAt(pos)
        if tree_item is None:
            return
        self.setCurrentItem(tree_item)
        item = tree_item.data(0, Qt.UserRole)
        menu = self.context_menu_for_item(item)
        menu.exec(self.viewport().mapToGlobal(pos))
