"""Shared depth-based visual styling for hierarchical QTreeWidgets.

Genre, Role, Mood, and Playlist trees all represent a parent/child hierarchy
and use the same visual language to show nesting: a colored dot icon per
depth level. Centralizing it here keeps the views visually consistent and
avoids re-implementing the same color table and tree setup in each one.
"""

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMessageBox, QTreeWidget
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger

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


def filter_tree_widget(tree: QTreeWidget, search_text: str) -> None:
    """Recursively hide tree items whose text (and all descendants' text)
    doesn't contain `search_text` (case-insensitive). An item stays visible
    if it matches directly or any descendant matches.
    """
    search_text = search_text.lower()

    def _filter_item(item) -> bool:
        text = item.text(0).lower()
        matches = search_text in text

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
    """Return the set of entity IDs (stored at `id_role`) whose tree item is
    currently expanded. Call before clearing/rebuilding a tree, together with
    `restore_expanded_ids`, to preserve the user's expand/collapse state
    across a reload.
    """
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


def restore_expanded_ids(
    tree: QTreeWidget, expanded_ids: set, *, id_role: int = Qt.UserRole
) -> None:
    """Re-expand tree items whose entity ID (stored at `id_role`) is in
    `expanded_ids`. Counterpart to `collect_expanded_ids`.
    """

    def _restore(item):
        entity_id = item.data(0, id_role)
        if entity_id in expanded_ids:
            item.setExpanded(True)
        for i in range(item.childCount()):
            _restore(item.child(i))

    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        _restore(root.child(i))


def restore_expanded_ids_or_expand_all(
    tree: QTreeWidget, expanded_ids: set, is_initial_load: bool, *, id_role: int = Qt.UserRole
) -> None:
    """After rebuilding `tree`, restore the expand state captured by
    `collect_expanded_ids`, or expand everything if this is the tree's
    first-ever population.

    An empty `expanded_ids` is ambiguous by itself: it means either "the
    user had fully collapsed the tree" (keep it collapsed) or "there's no
    saved state yet" (expand by default). `is_initial_load` disambiguates
    the two -- pass `tree.topLevelItemCount() == 0`, checked *before*
    `tree.clear()`.
    """
    if expanded_ids or not is_initial_load:
        restore_expanded_ids(tree, expanded_ids, id_role=id_role)
    else:
        tree.expandAll()


def insert_as_new_parent(controller, entity_type: str, id_attr: str, entity, new_entity) -> None:
    """Splice `new_entity` in as the parent of `entity`: it takes over
    `entity`'s old parent slot (preserving the grandparent chain), and
    `entity` becomes its child.
    """
    controller.update.update_entity(
        entity_type, getattr(new_entity, id_attr), parent_id=entity.parent_id
    )
    controller.update.update_entity(
        entity_type, getattr(entity, id_attr), parent_id=getattr(new_entity, id_attr)
    )


def insert_as_new_child(controller, entity_type: str, id_attr: str, entity, new_entity) -> None:
    """Set `new_entity` as a child of `entity`."""
    controller.update.update_entity(
        entity_type, getattr(new_entity, id_attr), parent_id=getattr(entity, id_attr)
    )


def handle_insert_as_new_relative(
    controller,
    parent_widget,
    *,
    entity_type: str,
    id_attr: str,
    name_attr: str,
    is_parent: bool,
    entity,
    new_entity,
    reload_fn,
    emit_fn=None,
    status_fn=None,
    exception_types: tuple = (SQLAlchemyError, RuntimeError),
    include_error_detail: bool = False,
) -> None:
    """Splice `new_entity` in via `insert_as_new_parent`/`insert_as_new_child`,
    then reload, notify, and report status -- the tail shared by every
    "create new parent/child" handler in this app (mood/genre/role tree views).

    Fetching `entity` and creating `new_entity` stay caller-specific -- those
    steps differ too much across entity types to share -- this only unifies
    the insert + reload + notify + error-handling that follows.

    `emit_fn(new_entity)` and `status_fn(message)`, if given, run only after a
    successful insert + reload, so callers can emit their own Qt signal and
    report status however they normally do (a toast vs. a status bar label).
    """
    relation = "parent" if is_parent else "child"
    label = entity_type.lower()
    try:
        if is_parent:
            insert_as_new_parent(controller, entity_type, id_attr, entity, new_entity)
        else:
            insert_as_new_child(controller, entity_type, id_attr, entity, new_entity)
        reload_fn()
        if emit_fn:
            emit_fn(new_entity)
        if status_fn:
            new_name = getattr(new_entity, name_attr)
            name = getattr(entity, name_attr)
            status_fn(f"Created '{new_name}' as {relation} of '{name}'")
    except exception_types as e:
        logger.error(f"Error creating new {relation} {label}: {e!s}")
        detail = f": {e!s}" if include_error_detail else ""
        QMessageBox.critical(
            parent_widget, "Error", f"Failed to create new {relation} {label}{detail}"
        )


def render_hierarchy_as_text(
    entities, *, id_attr: str, name_attr: str, parent_attr: str = "parent_id", sort_key=None
) -> str:
    """Render a flat list of hierarchical entities as a box-drawing tree,
    e.g.:

        Other
        ├── Singer & Songwriter
        └── Soundtrack
            ├── Film Score
            └── Musical

    Generic over attribute names so it works for Genre, Role, Mood, or any
    other self-referencing hierarchy (`id_attr`/`name_attr`/`parent_attr`
    name the primary-key, display-name, and parent-reference attributes on
    the given objects). `sort_key`, if given, orders siblings within each
    parent group (ascending) via `sorted(siblings, key=sort_key)`; omit it
    to preserve `entities`' given order.
    """
    children_map: dict = defaultdict(list)
    for entity in entities:
        children_map[getattr(entity, parent_attr)].append(entity)

    def _siblings(parent_id):
        kids = children_map.get(parent_id, [])
        return sorted(kids, key=sort_key) if sort_key else kids

    lines = []

    def _walk(entity, prefix, is_last, is_root):
        name = getattr(entity, name_attr)
        if is_root:
            lines.append(name)
            child_prefix = ""
        else:
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + name)
            child_prefix = prefix + ("    " if is_last else "│   ")

        kids = _siblings(getattr(entity, id_attr))
        for i, kid in enumerate(kids):
            _walk(kid, child_prefix, i == len(kids) - 1, is_root=False)

    roots = _siblings(None)
    for root in roots:
        _walk(root, "", True, is_root=True)

    return "\n".join(lines)


def is_hierarchy_descendant(
    ancestor_id, candidate_id, entities, *, id_attr: str, parent_attr: str = "parent_id"
) -> bool:
    """True if `candidate_id` is a descendant (at any depth) of `ancestor_id`
    within `entities` -- i.e. re-parenting `ancestor_id` under `candidate_id`
    would create a cycle. Use as a drag-drop reparent guard:
    `is_hierarchy_descendant(dragged_id, new_parent_id, ...)`.

    `entities` is the flat list of ORM objects for the whole hierarchy (e.g.
    all Genre/Mood/Role/Award rows); `id_attr`/`parent_attr` name the
    primary-key and parent-reference attributes on those objects.
    """
    if not ancestor_id or not candidate_id:
        return False

    children_by_parent: dict = {}
    for entity in entities:
        parent_id = getattr(entity, parent_attr, None)
        if parent_id is not None:
            children_by_parent.setdefault(parent_id, []).append(getattr(entity, id_attr))

    def _check(parent_id) -> bool:
        children = children_by_parent.get(parent_id, [])
        if candidate_id in children:
            return True
        return any(_check(child_id) for child_id in children)

    return _check(ancestor_id)
