"""
chart_entry_table.py

Shared QTreeWidget-based results table for chart entries, reused by both
ChartWeekBrowserTab and ChartSearchTab so column setup/rendering only lives
in one place. Follows the multi-column QTreeWidget convention used by
src/publisher/publisher_tree.py (setColumnCount + setHeaderLabels +
per-item setText(col, ...)) rather than QTableWidget.
"""

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem

from src.charts.chart_table_placeholder import install_empty_placeholder, sync_empty_placeholder
from src.common.match_confidence import confidence_color
from src.db.db_tables.chart import ChartEntry

_COLUMNS = ["Pos", "Title", "Artist", "Peak", "Weeks on Chart"]
_SORT_VALUE_ROLE = Qt.UserRole + 1
_NUMERIC_COLUMNS = {0, 3, 4}  # Pos, Peak, Weeks on Chart
_DOT_SIZE = 8


def _status_dot(color: QColor) -> QIcon:
    """Small filled-circle icon, drawn once per color, giving each row's
    match status a legible marker independent of the translucent row tint
    (which reads faintly under some themes -- see the class docstring)."""
    pixmap = QPixmap(_DOT_SIZE, _DOT_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(0, 0, _DOT_SIZE, _DOT_SIZE)
    painter.end()
    return QIcon(pixmap)


class _ChartEntryTreeItem(QTreeWidgetItem):
    """Sorts the Pos, Peak, and Weeks on Chart columns by their numeric
    value instead of lexicographically (so 9 sorts before 10), matching the
    pattern in genre_tree_builder.py's _GenreTreeItem."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else 0
        if column in _NUMERIC_COLUMNS:
            return self.data(column, _SORT_VALUE_ROLE) < other.data(column, _SORT_VALUE_ROLE)
        return self.text(column).lower() < other.text(column).lower()


class ChartEntryTable(QTreeWidget):
    """Flat (non-hierarchical) results list for ChartEntry rows.

    Match status is conveyed by row coloration rather than a dedicated
    column: matched rows are tinted via the house match_confidence.py
    convention (already used for MusicBrainz match review), unmatched rows
    are grayed out the same way disc_sorting.py grays virtual tracks --
    per-column setForeground() plus a tooltip, since no existing view in
    this codebase colors a whole row via a single call.

    setForeground() alone is not enough here: unlike the QTableWidget used
    by the MusicBrainz review UI, dark_mode.qss sets an explicit `color` on
    QTreeView::item, which wins over Qt::ForegroundRole for a QTreeWidget.
    A translucent setBackground() tint is added alongside it so the match
    state stays visible under the active theme.

    Manual match/clear-match is exposed as a right-click context menu whose
    two actions emit signals rather than touch the DB directly -- this
    widget stays controller-free like today, and the two host tabs
    (ChartWeekBrowserTab, ChartSearchTab) that already own a controller do
    the actual update + refresh().
    """

    manual_match_requested = Signal(int)  # chart_entry_id
    clear_match_requested = Signal(int)  # chart_entry_id

    def __init__(self, parent=None, empty_text: str = "No chart entries."):
        super().__init__(parent)
        self.setColumnCount(len(_COLUMNS))
        self.setHeaderLabels(_COLUMNS)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(True)
        self.setIconSize(QSize(_DOT_SIZE, _DOT_SIZE))
        # No column sorted at start, so results keep arriving in their
        # pre-ordered (by position/relevance) order until the user clicks a header.
        self.header().setSortIndicator(-1, Qt.AscendingOrder)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(2, QHeaderView.Stretch)  # Artist column grows
        self._entries_by_id = {}  # chart_entry_id -> ChartEntry, refreshed each populate()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._empty_label = install_empty_placeholder(self, empty_text)

    def populate(self, entries: Iterable[ChartEntry]) -> None:
        self.clear()
        self._entries_by_id = {}
        for entry in entries:
            item = _ChartEntryTreeItem(
                [str(entry.position), entry.raw_title, entry.raw_performer, str(entry.peak_position) if entry.peak_position else "", str(entry.weeks_on_chart) if entry.weeks_on_chart else ""]
            )
            item.setData(0, Qt.UserRole, entry.chart_entry_id)
            item.setData(0, _SORT_VALUE_ROLE, entry.position or 0)
            item.setData(3, _SORT_VALUE_ROLE, entry.peak_position or 0)
            item.setData(4, _SORT_VALUE_ROLE, entry.weeks_on_chart or 0)
            self._entries_by_id[entry.chart_entry_id] = entry
            if entry.is_matched:
                color = confidence_color(entry.match_score or 0.0)
                tint = QColor(color)
                tint.setAlpha(70)
                item.setIcon(0, _status_dot(color))
                for col in range(len(_COLUMNS)):
                    item.setForeground(col, color)
                    item.setBackground(col, QBrush(tint))
            else:
                tint = QColor(Qt.gray)
                tint.setAlpha(40)
                item.setIcon(0, _status_dot(QColor(Qt.gray)))
                for col in range(len(_COLUMNS)):
                    item.setForeground(col, Qt.gray)
                    item.setBackground(col, QBrush(tint))
                item.setToolTip(1, "Not yet matched to a library track")
            self.addTopLevelItem(item)
        sync_empty_placeholder(self, self._empty_label)

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
        match_action.triggered.connect(lambda: self.manual_match_requested.emit(entry_id))
        clear_action = menu.addAction("Clear Match")
        clear_action.setEnabled(entry.is_matched)
        clear_action.triggered.connect(lambda: self.clear_match_requested.emit(entry_id))
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
