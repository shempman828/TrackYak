"""Shared depth-based visual styling for hierarchical QTreeWidgets.

Genre, Role, Mood, and Playlist trees all represent a parent/child hierarchy
and use the same visual language to show nesting: a colored dot icon per
depth level, plus an indented "↳" prefix on the label. Centralizing it here
keeps the views visually consistent and avoids re-implementing the same
color table and tree setup in each one.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QTreeWidget

DEPTH_COLORS = [
    QColor(70, 130, 180),  # Steel Blue
    QColor(46, 139, 87),  # Sea Green
    QColor(218, 165, 32),  # Goldenrod
    QColor(178, 34, 34),  # Firebrick
    QColor(138, 43, 226),  # Blue Violet
    QColor(255, 140, 0),  # Dark Orange
    QColor(199, 21, 133),  # Medium Violet Red
    QColor(0, 191, 255),  # Deep Sky Blue
]
FALLBACK_COLOR = QColor(128, 128, 128)  # Gray, for depths beyond DEPTH_COLORS


def create_colored_icon(color: QColor, size: int = 16) -> QIcon:
    """Render a filled circle of `color` as a QIcon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(pixmap)


def icon_for_depth(depth: int, size: int = 16) -> QIcon:
    """Colored dot icon for a tree item at the given hierarchy depth."""
    color = DEPTH_COLORS[depth] if depth < len(DEPTH_COLORS) else FALLBACK_COLOR
    return create_colored_icon(color, size)


def hierarchy_label(text: str, depth: int) -> str:
    """Prefix `text` with the "↳" indent marker used for nested tree items."""
    if depth <= 0:
        return text
    return "  " * depth + "↳ " + text


def configure_hierarchy_tree(tree: QTreeWidget, *, multi_select: bool = True) -> None:
    """Apply the baseline config shared by hierarchy tree widgets: hidden
    header, multi-selection, animated expand/collapse, internal
    drag-and-drop reordering, and a custom-context-menu slot.
    """
    tree.setHeaderHidden(True)
    if multi_select:
        # ExtendedSelection: plain click selects only that item (clearing the
        # rest); Ctrl/Shift-click extends the selection. MultiSelection was
        # used here previously, but it toggles each clicked item without ever
        # clearing the others, so clicking through items left them all still
        # highlighted.
        tree.setSelectionMode(QTreeWidget.ExtendedSelection)
    tree.setAnimated(True)
    tree.setDragEnabled(True)
    tree.setAcceptDrops(True)
    tree.setDropIndicatorShown(True)
    tree.setDragDropMode(QTreeWidget.InternalMove)
    tree.setContextMenuPolicy(Qt.CustomContextMenu)
