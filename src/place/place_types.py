"""Place-type labels, colors, and type-filter helpers shared by the place
list (tree dots, type pills), the map (markers, legend), and the shared
type filter, so every surface names and colors a type the same way."""

import hashlib

# Pseudo-type bucket for places with no place_type set, so they can still be
# included/excluded via the type filter instead of always being shown.
NO_TYPE_LABEL = "No Type"

# Fixed colors for the common types; anything else gets a stable hashed
# color from _FALLBACK_PALETTE (see type_color).
TYPE_COLORS = {
    "country": "#2ecc71",
    "state": "#e67e22",
    "subdivision": "#00cec9",
    "county": "#f1c40f",
    "municipality": "#74b9ff",
    "city": "#3498db",
    "district": "#9b59b6",
    "venue": "#a29bfe",
    "studio": "#ff7675",
    "building": "#7f8c8d",
    "room": "#1abc9c",
    "point of interest": "#e74c3c",
}
NO_TYPE_COLOR = "#555e7a"
DEFAULT_TYPE_COLOR = "#8599ea"

_FALLBACK_PALETTE = ["#ff7675", "#6c5ce7", "#00b894", "#fdcb6e", "#e84393", "#00cec9", "#fab1a0", "#a29bfe"]


def type_label(place_type) -> str:
    """Normalize a raw place_type to its display/filter label ("city " -> "City")."""
    if place_type and str(place_type).strip():
        return str(place_type).strip().title()
    return NO_TYPE_LABEL


def type_color(label: str) -> str:
    """Hex color for a type label: fixed for common types, stable-hashed otherwise."""
    if not label or label == NO_TYPE_LABEL:
        return NO_TYPE_COLOR
    key = label.lower().strip()
    if key in TYPE_COLORS:
        return TYPE_COLORS[key]
    hash_val = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return _FALLBACK_PALETTE[hash_val % len(_FALLBACK_PALETTE)]


def order_types_by_hierarchy(places, labels) -> list[str]:
    """Order type labels broad-to-narrow (Country, State, ... Building).

    Ranks each type by the average depth at which its places sit in the
    real parent_id tree -- e.g. if City places tend to sit two levels below
    Country places, City comes after Country. Types with no depth signal
    fall back to TYPE_COLORS order, then alphabetical; "No Type" is last.
    """
    places_by_id = {p.place_id: p for p in places}
    depth_cache = {}

    def depth_of(place, seen):
        if place.place_id in depth_cache:
            return depth_cache[place.place_id]
        parent = places_by_id.get(place.parent_id) if place.parent_id is not None else None
        if parent is None or place.place_id in seen:
            depth_cache[place.place_id] = 0
            return 0
        depth = 1 + depth_of(parent, seen | {place.place_id})
        depth_cache[place.place_id] = depth
        return depth

    depths_by_type = {}
    for place in places:
        label = type_label(place.place_type)
        if label != NO_TYPE_LABEL:
            depths_by_type.setdefault(label, []).append(depth_of(place, set()))
    avg_depth = {t: sum(ds) / len(ds) for t, ds in depths_by_type.items()}
    static_rank = {t.title(): i for i, t in enumerate(TYPE_COLORS)}

    def sort_key(label):
        return (label == NO_TYPE_LABEL, avg_depth.get(label, float("inf")), static_rank.get(label, len(static_rank)), label)

    return sorted(labels, key=sort_key)


def merge_type_selection(previous_all: set, previous_selected: set, current_all: set, initial: set | None = None) -> set:
    """Carry a type-filter selection across a reload.

    On the first load (no previous_all) the selection is `initial` (e.g. a
    saved filter) or every type. Afterwards the old selection is kept for
    types that still exist, and types that are new since the last load
    start selected -- so a place saved with a brand-new type never
    disappears behind the filter.
    """
    if not previous_all:
        return (set(initial) & current_all) if initial is not None else set(current_all)
    return (previous_selected & current_all) | (current_all - previous_all)
