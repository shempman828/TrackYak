"""Shared builder for the "Add to Playlist" / "Add to Mood" context submenus.

The player dock (:mod:`src.player.player_context_menu`), the base track view
(:mod:`src.track.base_track_view`) and the main library Tracks tab
(:mod:`src.track.track_view_editing`) all offer a right-click submenu that lists
every playlist or mood, nested by ``parent_id``, alphabetised per level, with a
checkmark on the ones the track(s) already belong to. Historically each call
site hand-rolled its own version and they drifted (the base track view menu was
a flat, unsorted list with no hierarchy or checkmarks; the Tracks tab looked for
a ``parent_mood_id`` attribute that does not exist, so it never nested either).
This module is the one implementation they all share.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger

# Guard against a self-referential parent_id chain sending us infinitely deep.
_MAX_DEPTH = 8

# entity_type -> (id attribute, name attribute, plural label for placeholder rows)
_ENTITY_META = {
    "Playlist": ("playlist_id", "playlist_name", "playlists"),
    "Mood": ("mood_id", "mood_name", "moods"),
}


def selection_membership(tracks, relation_attr: str, id_attr: str):
    """Split entity ids by how much of a track selection already belongs.

    Returns ``(full, partial)``: ``full`` are ids every track in *tracks* is
    linked to (render checked), ``partial`` are ids only some of them are linked
    to (render with a " (partial)" suffix). ``relation_attr`` is the ``Track``
    relationship to walk (``"playlists"`` / ``"moods"``); ``id_attr`` is the id
    read off each link row (``"playlist_id"`` / ``"mood_id"``).
    """
    try:
        per_track = [
            {getattr(link, id_attr) for link in getattr(track, relation_attr, [])}
            for track in tracks
        ]
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
):
    """Fill *submenu* with a nested, alphabetically sorted tree of playlists/moods.

    Args:
        submenu: the ``QMenu`` to populate. The caller owns it and should have
            cleared it first.
        controller: app controller, used for ``controller.get.get_all_entities``.
        entity_type: ``"Playlist"`` or ``"Mood"``.
        on_trigger: slot connected to every action's ``triggered`` signal. It
            gets no useful argument; it should read ``self.sender().data()`` for
            the payload.
        member_ids: entity ids the track(s) already fully belong to -- rendered
            with a checkmark.
        partial_ids: entity ids only *some* of the selected tracks belong to --
            rendered unchecked with a " (partial)" suffix. Ignored when an id is
            also in ``member_ids``.
        make_action_data: optional ``callable(entity_id) -> value`` stashed on
            each action via ``setData``. Defaults to the bare entity id (what the
            player dock's handlers expect); the base track view passes a lambda
            returning ``(entity_id, track_ids)``.
        connection_type: Qt connection type for the ``triggered`` -> *on_trigger*
            link. The player dock needs ``Qt.QueuedConnection``.
    """
    id_attr, name_attr, plural = _ENTITY_META[entity_type]
    if make_action_data is None:

        def make_action_data(entity_id):
            return entity_id

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

    children_map: dict = {}
    for entity in entities:
        children_map.setdefault(getattr(entity, "parent_id", None), []).append(entity)
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

    def build_level(parent_menu: QMenu, parent_id, depth: int = 0):
        if depth > _MAX_DEPTH:
            return
        for entity in children_map.get(parent_id, []):
            entity_id = getattr(entity, id_attr)
            name = getattr(entity, name_attr, "") or ""
            if children_map.get(entity_id):
                branch = QMenu(name, parent_menu)
                build_level(branch, entity_id, depth + 1)
                branch.addSeparator()
                branch.addAction(make_action(branch, entity, label=f"Add to '{name}'"))
                parent_menu.addMenu(branch)
            else:
                parent_menu.addAction(make_action(parent_menu, entity, label=name))

    build_level(submenu, None)
