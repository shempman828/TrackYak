"""
chart_entry_table.py

Shared QTreeWidget-based results table for chart entries, reused by both
ChartWeekBrowserTab and ChartSearchTab so column setup/rendering only lives
in one place. Follows the multi-column QTreeWidget convention used by
src/publisher/publisher_tree.py (setColumnCount + setHeaderLabels +
per-item setText(col, ...)) rather than QTableWidget.
"""

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

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
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(False)  # results arrive pre-ordered (by position/relevance)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(2, QHeaderView.Stretch)  # Artist column grows

    def populate(self, entries: Iterable[ChartEntry]) -> None:
        self.clear()
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
