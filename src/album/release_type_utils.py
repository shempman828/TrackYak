"""
release_type_utils.py

Shared helper for normalizing an album's "release type" (Album, Single, EP,
Compilation, ...) so that case variants like "Album" and "album" collapse to
one canonical value instead of being treated as distinct types.
"""

RELEASE_TYPE_SUGGESTIONS = [
    "Album",
    "Single",
    "EP",
    "Compilation",
    "Soundtrack",
    "Live",
    "Remix",
    "Mixtape",
    "Bootleg",
    "Broadcast",
    "Demo",
]

_CANONICAL_BY_LOWER = {value.lower(): value for value in RELEASE_TYPE_SUGGESTIONS}


def normalize_release_type(value):
    """Trim whitespace and fold known release types onto their canonical casing."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    # Unrecognized values pass through unchanged (aside from trimming), since
    # they're user-defined types with no canonical form to match against.
    return _CANONICAL_BY_LOWER.get(stripped.lower(), stripped)
