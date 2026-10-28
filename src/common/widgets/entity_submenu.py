"""Shared builder for nested "Add to Playlist" / "Add to Mood" / parent-picker context submenus."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.qt_text import esc_amp
from src.foundation.logger_config import logger

# entity_type -> (id attribute, name attribute, plural label for placeholder rows)
_ENTITY_META = {
    "Playlist": ("playlist_id", "playlist_name", "playlists"),
    "Mood": ("mood_id", "mood_name", "moods"),
    "Role": ("role_id", "role_name", "roles"),
    "Genre": ("genre_id", "genre_name", "genres"),
}


def selection_membership(tracks, relation_attr: str, id_attr: str):
    """Return ``(full, partial)`` sets of entity ids linked to every / only some of *tracks*."""
    try:
        per_track = [{getattr(link, id_attr) for link in getattr(track, relation_attr, [])} for track in tracks]
    except SQLAlchemyError as e:
        logger.error(f"Error reading {relation_attr} membership for context menu: {e}")
        return set(), set()
    if not per_track:
        return set(), set()
    full = set.intersection(*per_track)
    return full, set.union(*per_track) - full


def populate_entity_submenu(
    submenu: QMenu,
    *,
    controller,
    entity_type: str,
    on_trigger,
    member_ids=frozenset(),
    partial_ids=frozenset(),
    make_action_data=None,
    connection_type=Qt.AutoConnection,
    entities_override=None,
    branch_self_label=None,
):
    """Fill *submenu* with a nested, per-level alphabetical tree of `entity_type` rows."""
    # on_trigger reads self.sender().data(): the bare id, or make_action_data(id).
    # entities_override skips the DB fetch (e.g. a cycle-safe parent list);
    # branch_self_label labels a branch's own action (default "Add to '<name>'").
    id_attr, name_attr, plural = _ENTITY_META[entity_type]
    if make_action_data is None:

        def make_action_data(entity_id):
            return entity_id

    if branch_self_label is None:

        def branch_self_label(disp):
            return f"Add to '{disp}'"

    if entities_override is not None:
        entities = entities_override
    else:
        try:
            entities = controller.get.get_all_entities(entity_type) or []
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading {plural} for context menu: {e}")
            submenu.addAction(f"Error loading {plural}").setEnabled(False)
            return

    if entity_type == "Playlist":
        # Smart playlists are rule-based; you can't drop a track into one by hand.
        entities = [e for e in entities if not getattr(e, "is_smart", 0)]

    if not entities:
        submenu.addAction(f"No {plural} available").setEnabled(False)
        return

    known_ids = {getattr(entity, id_attr) for entity in entities}
    children_map: dict = {}
    for entity in entities:
        parent_id = getattr(entity, "parent_id", None)
        # A parent missing from the list (deleted, or filtered out) makes this a root.
        children_map.setdefault(parent_id if parent_id in known_ids else None, []).append(entity)
    for siblings in children_map.values():
        siblings.sort(key=lambda e: (getattr(e, name_attr, "") or "").lower())

    def make_action(parent: QMenu, entity, *, label: str) -> QAction:
        entity_id = getattr(entity, id_attr)
        checked = entity_id in member_ids
        partial = not checked and entity_id in partial_ids
        action = QAction(f"{label} (partial)" if partial else label, parent)
        action.setData(make_action_data(entity_id))
        if checked or partial:
            action.setCheckable(True)
            action.setChecked(checked)
        action.triggered.connect(on_trigger, connection_type)
        return action

    visited: set = set()

    def add_entity(parent_menu: QMenu, entity):
        entity_id = getattr(entity, id_attr)
        if entity_id in visited:  # guards a corrupt parent_id cycle
            return
        visited.add(entity_id)
        name = getattr(entity, name_attr, "") or ""
        # Escape for display: QMenu titles and QAction text treat a lone
        # '&' as a mnemonic prefix and would otherwise eat it.
        disp = esc_amp(name)
        if children_map.get(entity_id):
            branch = QMenu(disp, parent_menu)
            build_level(branch, entity_id)
            branch.addSeparator()
            branch.addAction(make_action(branch, entity, label=branch_self_label(disp)))
            parent_menu.addMenu(branch)
        else:
            parent_menu.addAction(make_action(parent_menu, entity, label=disp))

    def build_level(parent_menu: QMenu, parent_id):
        for entity in children_map.get(parent_id, []):
            add_entity(parent_menu, entity)

    build_level(submenu, None)
    # Rows in a parent_id cycle are unreachable from any root; list them at the top level.
    for entity in sorted(entities, key=lambda e: (getattr(e, name_attr, "") or "").lower()):
        if getattr(entity, id_attr) not in visited:
            add_entity(submenu, entity)
