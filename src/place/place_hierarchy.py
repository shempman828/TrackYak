"""Parent/child helpers over an in-memory {place_id: Place} map, so no lookup triggers a lazy load."""

from sqlalchemy.orm import selectinload

from src.db.db_tables.place import Place

# Load option for get_all_entities("Place"): fetch every place's associations
# in one extra query, so association_counts() never lazy-loads per place.
PLACE_LOAD_OPTIONS = [selectinload(Place.associations)]


def children_map(places_by_id: dict) -> dict:
    """Map each place_id to the ids of its direct children."""
    children = {}
    for place in places_by_id.values():
        if place.parent_id in places_by_id:
            children.setdefault(place.parent_id, []).append(place.place_id)
    return children


def descendant_ids(places_by_id: dict, place_id) -> set:
    """Ids of every place below `place_id` (the place itself not included)."""
    children = children_map(places_by_id)
    found = set()
    stack = list(children.get(place_id, []))
    while stack:
        current = stack.pop()
        if current in found or current == place_id:  # cycle guard
            continue
        found.add(current)
        stack.extend(children.get(current, []))
    return found


def would_create_cycle(places_by_id: dict, place_ids, new_parent_id) -> bool:
    """True when making `new_parent_id` the parent of any of `place_ids` puts a place inside itself."""
    if new_parent_id is None:
        return False
    return any(new_parent_id == place_id or new_parent_id in descendant_ids(places_by_id, place_id) for place_id in place_ids)


def association_counts(places) -> dict:
    """Map place_id to (direct, recursive) association counts in one pass."""
    places_by_id = {p.place_id: p for p in places}
    direct = {p.place_id: len(p.associations) for p in places}
    children = children_map(places_by_id)
    recursive = {}

    def total(place_id, path):
        if place_id in recursive:
            return recursive[place_id]
        if place_id in path:  # parent_id cycle in the data
            return 0
        path.add(place_id)
        value = direct[place_id] + sum(total(child, path) for child in children.get(place_id, []))
        path.discard(place_id)
        recursive[place_id] = value
        return value

    for place_id in places_by_id:
        total(place_id, set())
    return {pid: (direct[pid], recursive[pid]) for pid in places_by_id}
