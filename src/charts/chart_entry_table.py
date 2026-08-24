"""
chart_entry_table.py

Shared QTreeWidget-based results table for chart entries, reused by both
ChartWeekBrowserTab and ChartSearchTab so column setup/rendering only lives
in one place. Follows the multi-column QTreeWidget convention used by
src/publisher/publisher_tree.py (setColumnCount + setHeaderLabels +
per-item setText(col, ...)) rather than QTableWidget.
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

from src.common.match_confidence import confidence_color
from src.db.db_tables.chart import ChartEntry

_COLUMNS = ["Pos", "Title", "Artist", "Peak", "Weeks on Chart"]


class ChartEntryTable(QTreeWidget):
    """Flat (non-hierarchical) results list for ChartEntry rows.

    Match status is conveyed by row coloration rather than a dedicated
    column: matched rows are tinted via the house match_confidence.py
    convention (already used for MusicBrainz match review), unmatched rows
    are grayed out the same way disc_sorting.py grays virtual tracks --
    per-column setForeground() plus a tooltip, since no existing view in
    this codebase colors a whole row via a single call.

    Manual match/clear-match is exposed as a right-click context menu whose
    two actions emit signals rather than touch the DB directly -- this
    widget stays controller-free like today, and the two host tabs
    (ChartWeekBrowserTab, ChartSearchTab) that already own a controller do
    the actual update + refresh().
    """

    manual_match_requested = Signal(int)  # chart_entry_id
    clear_match_requested = Signal(int)  # chart_entry_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(False)  # results arrive pre-ordered (by position/relevance)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(2, QHeaderView.Stretch)  # Artist column grows
        self._entries_by_id = {}  # chart_entry_id -> ChartEntry, refreshed each populate()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def populate(self, entries: Iterable[ChartEntry]) -> None:
        self.clear()
        self._entries_by_id = {}
        for entry in entries:
            item = QTreeWidgetItem(
                [
                    str(entry.position),
                    entry.raw_title,
                    entry.raw_performer,
                    str(entry.peak_position) if entry.peak_position else "",
                    str(entry.weeks_on_chart) if entry.weeks_on_chart else "",
                ]
            )
            item.setData(0, Qt.UserRole, entry.chart_entry_id)
            self._entries_by_id[entry.chart_entry_id] = entry
            if entry.is_matched:
                color = confidence_color(entry.match_score or 0.0)
                for col in range(len(_COLUMNS)):
                    item.setForeground(col, color)
            else:
                for col in range(len(_COLUMNS)):
                    item.setForeground(col, Qt.gray)
                item.setToolTip(1, "Not yet matched to a library track")
            self.addTopLevelItem(item)

    def selected_entry_id(self):
        """Return the chart_entry_id of the currently selected row, or None."""
        items = self.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def context_menu_for_entry(self, entry_id) -> QMenu | None:
        """Build (but don't show) the manual-match context menu for
        `entry_id`, or None if it's not a currently-populated row. Split out
        from _show_context_menu so tests can inspect/trigger the menu's
        actions without going through QMenu.exec()'s blocking popup loop."""
        entry = self._entries_by_id.get(entry_id)
        if entry is None:
            return None

        entity_type = entry.chart.matched_entity_type
        menu = QMenu(self)
        match_action = menu.addAction(f"Match to {entity_type}…")
        match_action.triggered.connect(
            lambda: self.manual_match_requested.emit(entry_id)
        )
        clear_action = menu.addAction("Clear Match")
        clear_action.setEnabled(entry.is_matched)
        clear_action.triggered.connect(
            lambda: self.clear_match_requested.emit(entry_id)
        )
        return menu

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        self.setCurrentItem(item)
        entry_id = item.data(0, Qt.UserRole)
        menu = self.context_menu_for_entry(entry_id)
        if menu is not None:
            menu.exec(self.viewport().mapToGlobal(pos))
