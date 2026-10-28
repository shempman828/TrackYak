"""Generic operations on self-referencing `parent_id` hierarchies (Genre, Role, Mood, Award, ...)."""

from collections import defaultdict, deque

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


def insert_as_new_parent(controller, entity_type: str, id_attr: str, entity, new_entity) -> bool:
    """Splice `new_entity` in as `entity`'s parent, under `entity`'s old parent; False on failure."""
    new_id = getattr(new_entity, id_attr)
    old_new_parent = getattr(new_entity, "parent_id", None)
    if not controller.update.update_entity(entity_type, new_id, parent_id=entity.parent_id):
        return False
    if controller.update.update_entity(entity_type, getattr(entity, id_attr), parent_id=new_id):
        return True
    # Undo the first step so new_entity is not left as a stray sibling.
    controller.update.update_entity(entity_type, new_id, parent_id=old_new_parent)
    return False


def insert_as_new_child(controller, entity_type: str, id_attr: str, entity, new_entity) -> bool:
    """Set `new_entity` as a child of `entity`; False on failure."""
    return bool(controller.update.update_entity(entity_type, getattr(new_entity, id_attr), parent_id=getattr(entity, id_attr)))


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
    """Insert `new_entity` as parent/child of `entity`, then reload, notify and report status."""
    # emit_fn(new_entity) and status_fn(message) run only after a successful insert + reload.
    relation = "parent" if is_parent else "child"
    label = entity_type.lower()
    insert = insert_as_new_parent if is_parent else insert_as_new_child
    try:
        inserted = insert(controller, entity_type, id_attr, entity, new_entity)
        if not inserted:
            logger.error(f"Error creating new {relation} {label}: update_entity failed")
            QMessageBox.critical(parent_widget, "Error", f"Failed to create new {relation} {label}")
            return
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
        QMessageBox.critical(parent_widget, "Error", f"Failed to create new {relation} {label}{detail}")


def render_hierarchy_as_text(entities, *, id_attr: str, name_attr: str, parent_attr: str = "parent_id", sort_key=None) -> str:
    """Render a flat list of hierarchical entities as a box-drawing (├── / └──) text tree."""
    # sort_key orders siblings; without it the given order is kept.
    known_ids = {getattr(entity, id_attr) for entity in entities}
    children_map: dict = defaultdict(list)
    for entity in entities:
        parent_id = getattr(entity, parent_attr)
        # A parent missing from the list makes this a root rather than dropping it.
        children_map[parent_id if parent_id in known_ids else None].append(entity)

    def _siblings(parent_id):
        kids = children_map.get(parent_id, [])
        return sorted(kids, key=sort_key) if sort_key else kids

    lines = []
    visited: set = set()

    def _walk(entity, prefix, is_last, is_root):
        visited.add(getattr(entity, id_attr))
        name = str(getattr(entity, name_attr) or "")
        if is_root:
            lines.append(name)
            child_prefix = ""
        else:
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + name)
            child_prefix = prefix + ("    " if is_last else "│   ")

        kids = [kid for kid in _siblings(getattr(entity, id_attr)) if getattr(kid, id_attr) not in visited]
        for i, kid in enumerate(kids):
            _walk(kid, child_prefix, i == len(kids) - 1, is_root=False)

    for root in _siblings(None):
        _walk(root, "", True, is_root=True)

    return "\n".join(lines)


def is_hierarchy_descendant(ancestor_id, candidate_id, entities, *, id_attr: str, parent_attr: str = "parent_id") -> bool:
    """True if `candidate_id` is a descendant of `ancestor_id` (re-parenting under it would make a cycle)."""
    if not ancestor_id or not candidate_id:
        return False
    return candidate_id in hierarchy_descendant_ids([ancestor_id], entities, id_attr=id_attr, parent_attr=parent_attr)


def hierarchy_descendant_ids(root_ids, entities, *, id_attr: str, parent_attr: str = "parent_id") -> set:
    """IDs of every descendant of any ID in `root_ids` (roots excluded unless nested in another)."""
    # One children map and one BFS: O(len(entities)). The `descendants` set
    # also stops a corrupt cyclic hierarchy from looping forever.
    children_by_parent: dict = defaultdict(list)
    for entity in entities:
        parent_id = getattr(entity, parent_attr, None)
        if parent_id is not None:
            children_by_parent[parent_id].append(getattr(entity, id_attr))

    descendants: set = set()
    queue = deque(root_ids)
    while queue:
        for child_id in children_by_parent.get(queue.popleft(), ()):
            if child_id not in descendants:
                descendants.add(child_id)
                queue.append(child_id)
    return descendants
