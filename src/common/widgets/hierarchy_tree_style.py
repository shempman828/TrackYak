"""Shared depth-based visual styling and expand/filter helpers for hierarchical QTreeWidgets."""

from functools import lru_cache

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
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
DEFAULT_DOT_SIZE = 18
TREE_INDENTATION = 20


def _color_for_depth(depth: int) -> QColor:
    """Color for a hierarchy depth; deeper than DEPTH_COLORS cycles in lighter/darker shades."""
    base = DEPTH_COLORS[depth % len(DEPTH_COLORS)]
    cycle = depth // len(DEPTH_COLORS)
    if cycle == 0:
        return base
    factor = 100 + ((cycle + 1) // 2) * 25
    return base.lighter(factor) if cycle % 2 else base.darker(factor)


def create_colored_icon(color: QColor, size: int = DEFAULT_DOT_SIZE) -> QIcon:
    """Render `color` as a soft ringed dot: a faint halo behind a gradient core with a darker rim."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    center = size / 2
    core_radius = size * 0.28
    halo_radius = size * 0.44

    halo = QColor(color)
    halo.setAlpha(70)
    painter.setBrush(QBrush(halo))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QPointF(center, center), halo_radius, halo_radius)

    gradient = QRadialGradient(QPointF(center - core_radius * 0.35, center - core_radius * 0.35), core_radius * 1.8)
    gradient.setColorAt(0.0, color.lighter(160))
    gradient.setColorAt(1.0, color)
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(color.darker(140), max(1.0, size * 0.06)))
    painter.drawEllipse(QPointF(center, center), core_radius, core_radius)

    painter.end()
    return QIcon(pixmap)


@lru_cache(maxsize=64)
def icon_for_depth(depth: int, size: int = DEFAULT_DOT_SIZE) -> QIcon:
    """Colored dot icon for a tree item at the given hierarchy depth (cached per depth/size)."""
    return create_colored_icon(_color_for_depth(depth), size)


def configure_hierarchy_tree(tree: QTreeWidget, *, multi_select: bool = True) -> None:
    """Apply the shared hierarchy-tree config: hidden header, DnD reorder, context menu, dot-icon sizing."""
    tree.setHeaderHidden(True)
    tree.setIndentation(TREE_INDENTATION)
    tree.setIconSize(QSize(DEFAULT_DOT_SIZE, DEFAULT_DOT_SIZE))
    if multi_select:
        # ExtendedSelection, not MultiSelection: a plain click must clear the
        # other selected items instead of toggling the clicked one.
        tree.setSelectionMode(QTreeWidget.ExtendedSelection)
    tree.setAnimated(True)
    tree.setDragEnabled(True)
    tree.setAcceptDrops(True)
    tree.setDropIndicatorShown(True)
    tree.setDragDropMode(QTreeWidget.InternalMove)
    tree.setContextMenuPolicy(Qt.CustomContextMenu)


def filter_tree_widget(tree: QTreeWidget, search_text: str) -> None:
    """Hide items that neither match `search_text` (case-insensitive) nor have a matching descendant."""
    search_text = search_text.lower()

    def _filter_item(item) -> bool:
        matches = search_text in item.text(0).lower()
        child_matches = False
        for i in range(item.childCount()):
            if _filter_item(item.child(i)):
                child_matches = True
        item.setHidden(not (matches or child_matches))
        return matches or child_matches

    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        _filter_item(root.child(i))


def collect_expanded_ids(tree: QTreeWidget, *, id_role: int = Qt.UserRole) -> set:
    """IDs (stored at `id_role`) of currently expanded items; capture before rebuilding the tree."""
    expanded_ids = set()

    def _collect(item):
        if item.isExpanded():
            entity_id = item.data(0, id_role)
            if entity_id is not None:
                expanded_ids.add(entity_id)
        for i in range(item.childCount()):
            _collect(item.child(i))

    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        _collect(root.child(i))
    return expanded_ids


def restore_expanded_ids(tree: QTreeWidget, expanded_ids: set, *, id_role: int = Qt.UserRole) -> None:
    """Re-expand items whose ID (stored at `id_role`) is in `expanded_ids`."""

    def _restore(item):
        if item.data(0, id_role) in expanded_ids:
            item.setExpanded(True)
        for i in range(item.childCount()):
            _restore(item.child(i))

    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        _restore(root.child(i))


def restore_expanded_ids_or_expand_all(tree: QTreeWidget, expanded_ids: set, is_initial_load: bool, *, id_role: int = Qt.UserRole) -> None:
    """Restore the captured expand state, or expand everything on the tree's first population."""
    # An empty set alone is ambiguous (fully collapsed vs. no state yet); pass
    # is_initial_load = tree.topLevelItemCount() == 0, checked before tree.clear().
    if expanded_ids or not is_initial_load:
        restore_expanded_ids(tree, expanded_ids, id_role=id_role)
    else:
        tree.expandAll()
