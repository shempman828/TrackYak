"""Shared helpers for the canonical PlaceAssociationType list."""

from src.common.entity_completer_edit import find_or_create_by_name
from src.core.logger_config import logger


def fetch_association_types(controller):
    """Fetch all canonical place association types, sorted by name."""
    try:
        types = controller.get.get_all_entities("PlaceAssociationType") or []
        return sorted(types, key=lambda t: (t.type_name or "").lower())
    except Exception as e:
        logger.warning(f"Could not fetch place association types: {e}")
        return []


def find_or_create_association_type(controller, name, known_types):
    """
    Look up an association type by name (case-insensitive) among known
    types; create one only if none is found. Matching against the
    already-loaded list guarantees an existing type always wins over
    creating a same-named duplicate, regardless of case. Returns None for
    a blank/whitespace-only name.
    """
    name = (name or "").strip()
    if not name:
        return None
    return find_or_create_by_name(
        controller, "PlaceAssociationType", "type_name", name, known_types
    )
