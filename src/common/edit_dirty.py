"""Shared "did this field actually change?" comparison for entity editors.

Track's editor (src/track/track_edit_basetab.py) pioneered this comparison;
Album and Artist editors reuse it here instead of re-declaring their own copy.
"""


def value_changed(old, new) -> bool:
    """Return True if `new` differs meaningfully from `old` (the value loaded
    from the database). None and "empty" defaults (empty string, 0, 0.0,
    False) are treated as equivalent so an unset field isn't reported as
    changed just because a widget's blank value doesn't match None exactly.
    """
    if old is None and new in (None, "", 0, 0.0, False):
        return False
    return str(old).strip() != str(new).strip()
