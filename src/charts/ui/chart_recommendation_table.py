"""Flat results table of aggregated MissingChartItem rows for the Recommendations tab."""

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeWidget

from src.charts.chart_recommendations import MISSING_PEAK_RANK, MissingChartItem
from src.charts.ui.chart_table_placeholder import SORT_VALUE_ROLE, NumericSortTreeItem, install_empty_placeholder, populate_rows

_COLUMNS = ["Title", "Artist", "Type", "Chart", "Peak", "Weeks on Chart", "Connects"]
_NUMERIC_COLUMNS = frozenset({4, 5, 6})  # Peak, Weeks on Chart, Connects
_TITLE_WIDTH = 260
_ARTIST_WIDTH = 200


class ChartRecommendationTable(QTreeWidget):
    """Flat MissingChartItem list with a right-click "Match to…" action for the whole group."""

    # Every row is unmatched by construction, so there is no "Clear Match" action.
    bulk_match_requested = Signal(object)  # MissingChartItem
    numeric_columns = _NUMERIC_COLUMNS

    def __init__(self, parent=None, empty_text: str = "No recommendations found."):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(True)
        # No sorted column at start, so rows keep their pre-ranked order until a header click.
        self.header().setSortIndicator(-1, Qt.AscendingOrder)
        # Interactive (not Stretch) so every column border can be dragged.
        self.header().setSectionResizeMode(QHeaderView.Interactive)
        self.setColumnWidth(0, _TITLE_WIDTH)
        self.setColumnWidth(1, _ARTIST_WIDTH)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._empty_label = install_empty_placeholder(self, empty_text)

    def populate(self, items: Iterable[MissingChartItem]) -> None:
        """Replace the rows with items."""
        populate_rows(self, self._empty_label, items, self._add_item)

    def _add_item(self, item: MissingChartItem) -> None:
        """Add one recommendation as a row."""
        # Connects (gap_run_length) is only set for Gap Fills results.
        tree_item = NumericSortTreeItem(
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
        tree_item.setData(4, SORT_VALUE_ROLE, item.peak_position if item.peak_position is not None else MISSING_PEAK_RANK)
        tree_item.setData(5, SORT_VALUE_ROLE, item.weeks_on_chart or 0)
        tree_item.setData(6, SORT_VALUE_ROLE, item.gap_run_length or 0)
        self.addTopLevelItem(tree_item)

    def context_menu_for_item(self, item: MissingChartItem) -> QMenu:
        """Build (without showing) the match menu for item."""
        menu = QMenu(self)
        match_action = menu.addAction(f"Match to {item.entity_type}…")
        match_action.triggered.connect(lambda: self.bulk_match_requested.emit(item))
        return menu

    def _show_context_menu(self, pos) -> None:
        """Show the match menu for the row under pos."""
        tree_item = self.itemAt(pos)
        if tree_item is None:
            return
        self.setCurrentItem(tree_item)
        menu = self.context_menu_for_item(tree_item.data(0, Qt.UserRole))
        menu.exec(self.viewport().mapToGlobal(pos))
