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

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
)

from src.charts.chart_recommendations import MissingChartItem

_COLUMNS = ["Title", "Artist", "Type", "Chart", "Peak", "Weeks on Chart", "Connects"]


class ChartRecommendationTable(QTreeWidget):
    bulk_match_requested = Signal(object)  # MissingChartItem

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(False)  # results arrive pre-ranked
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Artist column grows
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def populate(self, items: Iterable[MissingChartItem]) -> None:
        self.clear()
        for item in items:
            tree_item = QTreeWidgetItem(
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
            self.addTopLevelItem(tree_item)

    def context_menu_for_item(self, item: MissingChartItem) -> QMenu:
        """Build (but don't show) the manual-match context menu for `item`.
        Split out from _show_context_menu so tests can inspect/trigger the
        menu's action without going through QMenu.exec()'s blocking popup
        loop (mirrors ChartEntryTable.context_menu_for_entry)."""
        menu = QMenu(self)
        match_action = menu.addAction(f"Match to {item.entity_type}…")
        match_action.triggered.connect(
            lambda: self.bulk_match_requested.emit(item)
        )
        return menu

    def _show_context_menu(self, pos) -> None:
        tree_item = self.itemAt(pos)
        if tree_item is None:
            return
        self.setCurrentItem(tree_item)
        item = tree_item.data(0, Qt.UserRole)
        menu = self.context_menu_for_item(item)
        menu.exec(self.viewport().mapToGlobal(pos))
