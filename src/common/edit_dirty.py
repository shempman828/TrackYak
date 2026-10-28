"""Shared "did this field actually change?" comparison for entity editors."""

_EMPTY_VALUES = (None, "", 0, 0.0, False)


def value_changed(old, new) -> bool:
    """Return True if `new` differs meaningfully from `old` (the value loaded from the DB)."""
    # A NULL column and a widget's blank default ("", 0, False) are the same "unset" value.
    if old is None and (new in _EMPTY_VALUES or (isinstance(new, str) and not new.strip())):
        return False
    old_text = "" if old is None else str(old).strip()
    new_text = "" if new is None else str(new).strip()
    return old_text != new_text
