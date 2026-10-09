"""
SyncSelectionTree -- the playlist/mood checklist on the Sync view's Music page.

A two-column QTreeWidget: the item name, and a right-aligned muted track
count. Top-level rows are the PLAYLISTS / MOODS section headers, whose count
column reads "N of M selected".

Each playlist/mood is selected independently -- a parent playlist can hold
tracks of its own, so ticking it must not imply its children. A parent that
is itself unticked but has ticked descendants is shown PartiallyChecked as a
"something inside is selected" hint; everything that reads the selection
compares against Qt.Checked, so the hint never counts as a selection.

Also owns the name filter (matches stay visible together with their
ancestors) and paints a centred placeholder ("Loading…", "No matches") when
there are no rows to show.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem

from src.common.widgets.item_foreground_delegate import ItemForegroundDelegate

# Theme colours (see the palette header in themes/dark_mode.qss). Per-column
# item colours can't be reached from QSS, so they are set on the items.
_MUTED = QColor("#555e7a")
_ACCENT = QColor("#8599ea")

NAME_COLUMN = 0
COUNT_COLUMN = 1


class SyncSelectionTree(QTreeWidget):
    # Emitted after a context-menu "with sub-items" bulk change, which runs
    # with signals blocked and so produces no itemChanged.
    bulkCheckChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SyncSelectionTree")
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setItemDelegate(ItemForegroundDelegate(self))
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(NAME_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(COUNT_COLUMN, QHeaderView.ResizeToContents)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._placeholder = ""
        self._filter_text = ""

    # -- building rows ---------------------------------------------------------

    def add_section(self, title: str) -> QTreeWidgetItem:
        """A non-selectable PLAYLISTS / MOODS header row."""
        item = QTreeWidgetItem(self, [title, ""])
        item.setFlags(Qt.ItemIsEnabled)
        font = QFont()
        font.setBold(True)
        item.setFont(NAME_COLUMN, font)
        item.setForeground(NAME_COLUMN, _ACCENT)
        item.setForeground(COUNT_COLUMN, _MUTED)
        item.setTextAlignment(COUNT_COLUMN, Qt.AlignRight | Qt.AlignVCenter)
        return item

    @staticmethod
    def make_item(parent: QTreeWidgetItem, data: dict) -> QTreeWidgetItem:
        """A checkable playlist/mood row carrying `data` in UserRole."""
        smart_only = data.get("is_smart") and not data.get("track_count")
        count = "smart" if smart_only else f"{data.get('track_count', 0):,}"
        item = QTreeWidgetItem(parent, [data["name"], count])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(NAME_COLUMN, Qt.Unchecked)
        item.setToolTip(NAME_COLUMN, data.get("description") or "")
        item.setData(NAME_COLUMN, Qt.UserRole, data)
        item.setForeground(COUNT_COLUMN, _MUTED)
        item.setTextAlignment(COUNT_COLUMN, Qt.AlignRight | Qt.AlignVCenter)
        return item

    def set_placeholder_text(self, text: str) -> None:
        """Text painted in the viewport while no row is visible."""
        self._placeholder = text
        self.viewport().update()

    # -- selection hints & section counts --------------------------------------

    def refresh_partial_states(self) -> None:
        """Mark unticked parents that have ticked descendants as PartiallyChecked."""
        blocked = self.blockSignals(True)

        def walk(item: QTreeWidgetItem) -> bool:
            """Return True if `item` or any descendant is ticked."""
            any_child = False
            for i in range(item.childCount()):
                any_child = walk(item.child(i)) or any_child
            if item.data(NAME_COLUMN, Qt.UserRole) is None:
                return any_child  # section header / invisible root
            state = item.checkState(NAME_COLUMN)
            if state != Qt.Checked:
                hint = Qt.PartiallyChecked if any_child else Qt.Unchecked
                if state != hint:
                    item.setCheckState(NAME_COLUMN, hint)
            return state == Qt.Checked or any_child

        walk(self.invisibleRootItem())
        self.blockSignals(blocked)

    def update_section_counts(self) -> None:
        """Write "N of M selected" into each section header's count column."""
        blocked = self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            section = self.topLevelItem(i)
            total = selected = 0
            for item in self._descendants(section):
                total += 1
                if item.checkState(NAME_COLUMN) == Qt.Checked:
                    selected += 1
            section.setText(COUNT_COLUMN, f"{selected} of {total} selected" if total else "")
        self.blockSignals(blocked)

    # -- filter ----------------------------------------------------------------

    def set_filter_text(self, text: str) -> None:
        """Show only rows whose name contains `text` (plus their ancestors)."""
        self._filter_text = text.strip().casefold()
        needle = self._filter_text

        def apply(item: QTreeWidgetItem) -> bool:
            child_match = False
            for i in range(item.childCount()):
                child_match = apply(item.child(i)) or child_match
            if item.data(NAME_COLUMN, Qt.UserRole) is None:
                return child_match
            match = not needle or needle in item.text(NAME_COLUMN).casefold()
            item.setHidden(not (match or child_match))
            if needle and child_match:
                item.setExpanded(True)
            return match or child_match

        for i in range(self.topLevelItemCount()):
            section = self.topLevelItem(i)
            section.setHidden(bool(needle) and not apply(section))
            if not needle:
                apply(section)
        self.viewport().update()

    def filter_text(self) -> str:
        return self._filter_text

    def visible_checkable_items(self):
        """Every playlist/mood row the filter currently shows."""
        for i in range(self.topLevelItemCount()):
            for item in self._descendants(self.topLevelItem(i)):
                if not self._is_filtered_out(item):
                    yield item

    def has_visible_rows(self) -> bool:
        return any(True for _ in self.visible_checkable_items())

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _descendants(item: QTreeWidgetItem):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(NAME_COLUMN, Qt.UserRole) is not None:
                yield child
            yield from SyncSelectionTree._descendants(child)

    @staticmethod
    def _is_filtered_out(item: QTreeWidgetItem) -> bool:
        while item is not None:
            if item.isHidden():
                return True
            item = item.parent()
        return False

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None or item.data(NAME_COLUMN, Qt.UserRole) is None or not item.childCount():
            return
        menu = QMenu(self)
        select = QAction("Select with all sub-items", menu)
        select.triggered.connect(lambda: self._set_branch(item, Qt.Checked))
        clear = QAction("Clear with all sub-items", menu)
        clear.triggered.connect(lambda: self._set_branch(item, Qt.Unchecked))
        menu.addAction(select)
        menu.addAction(clear)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _set_branch(self, item: QTreeWidgetItem, state) -> None:
        blocked = self.blockSignals(True)
        item.setCheckState(NAME_COLUMN, state)
        for child in self._descendants(item):
            child.setCheckState(NAME_COLUMN, state)
        self.blockSignals(blocked)
        self.bulkCheckChanged.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._placeholder or self.has_visible_rows():
            return
        painter = QPainter(self.viewport())
        painter.setPen(_MUTED)
        rect = self.viewport().rect().adjusted(24, 24, -24, -24)
        painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self._placeholder)
        painter.end()
