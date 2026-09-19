"""
playlist_smart_criteria_fields.py

Static field/operator configuration for smart playlist criteria rows.
"""

from datetime import datetime

from src.db.db_mapping_tracks import TRACK_FIELDS
from src.db.field_spec import FieldSpec

# ---------------------------------------------------------------------------
# Fields to exclude from smart playlist filtering (internal / not filterable)
# ---------------------------------------------------------------------------
_EXCLUDED_FIELDS = {
    "track_id",
    "track_file_path",
    "MBID",
    "track_barcode",
    "track_wikipedia_link",
    "lyrics",  # too long to filter meaningfully
    "track_gain",  # internal audio normalization value
    "track_peak",  # internal audio normalization value
}

# ---------------------------------------------------------------------------
# Extra "List" fields that are association proxies on the Track model.
# These aren't in TRACK_FIELDS (they're relationships, not scalar columns)
# but are very useful for smart playlist filtering.
# ---------------------------------------------------------------------------
_LIST_FIELDS = [
    ("genre_names", "Genre Names", "Filter by genre, e.g.: Rock, Jazz"),
    ("artist_names", "Artist Names", "Filter by any associated artist name"),
    # "primary_artist_names" is intentionally omitted here — it's already in
    # TRACK_FIELDS (category "Basic") as "Primary Artist(s)".
    ("place_names", "Place Names", "Filter by associated place"),
    ("mood_name", "Mood", "Filter by mood"),
]


# ---------------------------------------------------------------------------
# Map a FieldSpec's Python type → our operator-group key
# ---------------------------------------------------------------------------
def _field_to_group(field: FieldSpec) -> str:
    t = field.type
    if t is int:
        return "Integer"
    if t is float:
        return "Float"
    if t is bool:
        return "Bool"
    if t is datetime:
        return "Datetime"
    return "String"


# ---------------------------------------------------------------------------
# Build the ordered field list from TRACK_FIELDS + _LIST_FIELDS, grouped by
# FieldSpec.category — mirrors the grouping TrackView uses for its column
# picker (src/track/track_view_columns.py::show_column_menu).
# Each entry: (field_name, op_group, display_name, tooltip, min, max, category)
# ---------------------------------------------------------------------------
def _build_criteria_fields():
    entries = []

    # Preferred category order for the most commonly filtered fields; any
    # other categories present in TRACK_FIELDS are appended alphabetically
    # after these, followed by an "Other" catch-all.
    category_order = [
        "Basic",
        "Properties",
        "Date",
        "User",
        "Advanced",
        "Classical",
        "Identification",
    ]

    buckets: dict[str, list] = {}
    for field_name, field in TRACK_FIELDS.items():
        if field_name in _EXCLUDED_FIELDS:
            continue
        cat = field.category or "Other"
        buckets.setdefault(cat, []).append((field_name, field))

    remaining = sorted(c for c in buckets if c not in category_order and c != "Other")
    ordered_categories = [c for c in category_order if c in buckets] + remaining
    if "Other" in buckets:
        ordered_categories.append("Other")

    for cat in ordered_categories:
        for field_name, field in buckets[cat]:
            entries.append(
                (
                    field_name,
                    _field_to_group(field),
                    field.friendly or field_name,
                    field.tooltip or "",
                    field.min,
                    field.max,
                    cat,
                )
            )

    # Append relationship / list fields under their own "Related" group
    for field_name, display, tooltip in _LIST_FIELDS:
        entries.append((field_name, "List", display, tooltip, None, None, "Related"))

    return entries


CRITERIA_FIELDS = _build_criteria_fields()

# ---------------------------------------------------------------------------
# Operators available per group — only logically valid operators are shown
# ---------------------------------------------------------------------------
OPERATORS_BY_GROUP = {
    "String": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("contains", "contains"),
        ("startswith", "starts with"),
        ("endswith", "ends with"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Integer": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("gt", "greater than"),
        ("lt", "less than"),
        ("gte", "greater than or equal"),
        ("lte", "less than or equal"),
        ("range", "between (inclusive)"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Float": [
        ("eq", "equals"),
        ("not", "does not equal"),
        ("gt", "greater than"),
        ("lt", "less than"),
        ("gte", "greater than or equal"),
        ("lte", "less than or equal"),
        ("range", "between (inclusive)"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "Bool": [("eq", "is"), ("not", "is not"), ("isnull", "is empty"), ("notnull", "has a value")],
    "Datetime": [
        ("gt", "after"),
        ("lt", "before"),
        ("gte", "on or after"),
        ("lte", "on or before"),
        ("eq", "on this day"),
        ("range", "between (inclusive)"),
        ("last_n_days", "in the last N days"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
    "List": [
        ("in", "is one of"),
        ("not_in", "is not one of"),
        ("contains", "contains"),
        ("isnull", "is empty"),
        ("notnull", "has a value"),
    ],
}
