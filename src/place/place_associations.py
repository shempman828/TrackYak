"""Query and describe the music connected to a place: associations, their entities, names, and tooltips."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.db.db_tables.place import PlaceAssociation
from src.foundation.logger_config import logger

# Groups with more rows than this start collapsed, so a large group does not flood the list.
GROUP_AUTO_EXPAND_THRESHOLD = 10

# Load each association's type with the association, not one query per row.
_ASSOCIATION_LOAD_OPTIONS = [selectinload(PlaceAssociation.association_type)]


def fetch_place_associations(controller, place_id, recursive=False):
    """Associations of a place; with `recursive`, also of every descendant, each tagged with `place_path` ("A → B → C")."""
    if not recursive:
        return controller.get.get_all_entities("PlaceAssociation", load_options=_ASSOCIATION_LOAD_OPTIONS, place_id=place_id)

    places_by_id = {p.place_id: p for p in controller.get.get_all_entities("Place")}
    if place_id not in places_by_id:
        return []
    children = {}
    for place in places_by_id.values():
        children.setdefault(place.parent_id, []).append(place.place_id)

    paths = {place_id: [places_by_id[place_id].place_name]}
    stack = [place_id]
    while stack:
        current = stack.pop()
        for child_id in children.get(current, []):
            if child_id not in paths:  # guards against a parent_id cycle in the data
                paths[child_id] = [*paths[current], places_by_id[child_id].place_name]
                stack.append(child_id)

    associations = controller.get.get_all_entities("PlaceAssociation", load_options=_ASSOCIATION_LOAD_OPTIONS, place_id__in=list(paths))
    for assoc in associations:
        assoc.place_path = " → ".join(paths.get(assoc.place_id, []))
    return associations


def fetch_entities(controller, associations) -> dict:
    """Map (entity_type, entity_id) to the entity each association points at, one query per entity type."""
    ids_by_type = {}
    for assoc in associations:
        if assoc.entity_type:
            ids_by_type.setdefault(assoc.entity_type, set()).add(assoc.entity_id)

    entities = {}
    for entity_type, ids in ids_by_type.items():
        id_attr = f"{entity_type.lower()}_id"
        try:
            rows = controller.get.get_all_entities(entity_type.title(), **{f"{id_attr}__in": list(ids)})
        except SQLAlchemyError:
            logger.exception("Error getting %s entities for place associations", entity_type)
            continue
        for row in rows or []:
            entities[(entity_type, getattr(row, id_attr, None))] = row
    return entities


def entity_display_name(entity, entity_type):
    """Display name of an associated entity (its `<type>_name` attribute)."""
    if not entity or not entity_type:
        return f"Unknown {entity_type or 'entity'}"
    return getattr(entity, f"{entity_type.lower()}_name", getattr(entity, "name", f"Unknown {entity_type}"))


def entity_tooltip(entity, entity_type):
    """Multi-line tooltip with the most useful facts about an associated entity."""
    entity_type = (entity_type or "").lower()
    if entity_type == "artist" and hasattr(entity, "artist_name"):
        tooltip = f"Artist: {entity.artist_name}\n"
        tooltip += f"Type: {'Group' if entity.isgroup else 'Person'}\n"
        if entity.begin_year:
            tooltip += f"Born: {entity.begin_year}"
            if entity.end_year:
                tooltip += f" - Died: {entity.end_year}"
        return tooltip
    if entity_type == "track" and hasattr(entity, "track_name"):
        tooltip = f"Track: {entity.track_name}\n"
        if getattr(entity, "album", None):
            tooltip += f"Album: {entity.album.album_name}\n"
        if entity.duration:
            tooltip += f"Duration: {entity.duration_formatted}"
        return tooltip
    if entity_type == "album" and hasattr(entity, "album_name"):
        tooltip = f"Album: {entity.album_name}\n"
        if entity.release_year:
            tooltip += f"Released: {entity.release_year}"
        return tooltip
    if entity_type == "publisher" and hasattr(entity, "publisher_name"):
        return f"Publisher: {entity.publisher_name}"
    if entity_type == "playlist" and hasattr(entity, "playlist_name"):
        tooltip = f"Playlist: {entity.playlist_name}\n"
        if entity.playlist_description:
            tooltip += f"Description: {entity.playlist_description}"
        return tooltip
    return f"{entity_type.title()}: {getattr(entity, 'name', 'Unknown')}"


def group_associations(controller, place_id, recursive=False) -> dict:
    """Map entity type to sorted [(association, entity)] rows; entity is None when it no longer exists."""
    associations = fetch_place_associations(controller, place_id, recursive=recursive)
    entities = fetch_entities(controller, associations)
    groups = {}
    for assoc in associations:
        entity = entities.get((assoc.entity_type, assoc.entity_id))
        groups.setdefault(assoc.entity_type or "Unknown", []).append((assoc, entity))
    for rows in groups.values():
        rows.sort(key=lambda r: str(entity_display_name(r[1], r[0].entity_type) if r[1] else "").lower())
    return groups
