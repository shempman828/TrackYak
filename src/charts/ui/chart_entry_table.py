"""Flat results table of ChartEntry rows, shared by the Week Browser and Search tabs."""

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeWidget

from src.charts.chart_recommendations import MISSING_PEAK_RANK
from src.charts.ui.chart_table_placeholder import SORT_VALUE_ROLE, NumericSortTreeItem, install_empty_placeholder, populate_rows
from src.common.match_confidence import confidence_color
from src.db.db_tables.chart import ChartEntry

_COLUMNS = ["Pos", "Title", "Artist", "Peak", "Weeks on Chart"]
# Extra columns for a result set that spans charts and weeks (the Search tab).
_CONTEXT_COLUMNS = ["Chart", "Week"]
_NUMERIC_COLUMNS = frozenset({0, 3, 4})  # Pos, Peak, Weeks on Chart
_DOT_SIZE = 8
_dot_cache: dict[int, QIcon] = {}


def _status_dot(color: QColor) -> QIcon:
    """Return a small filled-circle icon in color, cached per RGBA value."""
    key = color.rgba()
    icon = _dot_cache.get(key)
    if icon is None:
        pixmap = QPixmap(_DOT_SIZE, _DOT_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(0, 0, _DOT_SIZE, _DOT_SIZE)
        painter.end()
        icon = _dot_cache[key] = QIcon(pixmap)
    return icon


class ChartEntryTable(QTreeWidget):
    """Flat ChartEntry list with match status shown by row color and a right-click match menu."""

    manual_match_requested = Signal(int)  # chart_entry_id
    clear_match_requested = Signal(int)  # chart_entry_id
    numeric_columns = _NUMERIC_COLUMNS

    def __init__(self, parent=None, empty_text: str = "No chart entries.", show_context: bool = False):
        super().__init__(parent)
        self._columns = _COLUMNS + (_CONTEXT_COLUMNS if show_context else [])
        self._show_context = show_context
        self.setColumnCount(len(self._columns))
        self.setHeaderLabels(self._columns)
        self.setRootIsDecorated(False)  # flat list, no expand arrows
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(True)
        self.setIconSize(QSize(_DOT_SIZE, _DOT_SIZE))
        # No sorted column at start, so rows keep their pre-ordered order until a header click.
        self.header().setSortIndicator(-1, Qt.AscendingOrder)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)  # Title column grows
        self.header().setSectionResizeMode(2, QHeaderView.Stretch)  # Artist column grows
        self._entries_by_id = {}  # chart_entry_id -> ChartEntry, refreshed each populate()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._empty_label = install_empty_placeholder(self, empty_text)

    def populate(self, entries: Iterable[ChartEntry]) -> None:
        """Replace the rows with entries."""
        self._entries_by_id = {}
        populate_rows(self, self._empty_label, entries, self._add_entry)

    def _add_entry(self, entry: ChartEntry) -> None:
        """Add one entry as a row, colored by its match status."""
        texts = [str(entry.position), entry.raw_title, entry.raw_performer, str(entry.peak_position) if entry.peak_position else "", str(entry.weeks_on_chart) if entry.weeks_on_chart else ""]
        if self._show_context:
            texts += [entry.chart.chart_name if entry.chart else "", entry.chart_week.isoformat() if entry.chart_week else ""]
        item = NumericSortTreeItem(texts)
        item.setData(0, Qt.UserRole, entry.chart_entry_id)
        item.setData(0, SORT_VALUE_ROLE, entry.position or 0)
        item.setData(3, SORT_VALUE_ROLE, entry.peak_position if entry.peak_position is not None else MISSING_PEAK_RANK)
        item.setData(4, SORT_VALUE_ROLE, entry.weeks_on_chart or 0)
        self._entries_by_id[entry.chart_entry_id] = entry

        if entry.is_matched:
            color = confidence_color(entry.match_score or 0.0)
            tint = QColor(color)
            tint.setAlpha(70)
        else:
            color = QColor(Qt.gray)
            tint = QColor(Qt.gray)
            tint.setAlpha(40)
            item.setToolTip(1, "Not yet matched to a library track")
        item.setIcon(0, _status_dot(color))
        # dark_mode.qss sets `color` on QTreeView::item, which beats setForeground(),
        # so a translucent background tint keeps the match state visible.
        brush = QBrush(tint)
        for col in range(len(self._columns)):
            item.setForeground(col, color)
            item.setBackground(col, brush)
        self.addTopLevelItem(item)

    def context_menu_for_entry(self, entry_id) -> QMenu | None:
        """Build (without showing) the match menu for entry_id, or return None for an unknown row."""
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
        """Show the match menu for the row under pos."""
        item = self.itemAt(pos)
        if item is None:
            return
        self.setCurrentItem(item)
        menu = self.context_menu_for_entry(item.data(0, Qt.UserRole))
        if menu is not None:
            menu.exec(self.viewport().mapToGlobal(pos))
