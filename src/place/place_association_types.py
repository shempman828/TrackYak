"""Shared helpers for the canonical PlaceAssociationType list."""

from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_edit import find_or_create_by_name
from src.foundation.logger_config import logger


def fetch_association_types(controller):
    """Fetch all canonical place association types, sorted by name."""
    try:
        types = controller.get.get_all_entities("PlaceAssociationType") or []
        return sorted(types, key=lambda t: (t.type_name or "").lower())
    except SQLAlchemyError as e:
        logger.warning(f"Could not fetch place association types: {e}")
        return []


def find_or_create_association_type(controller, name, known_types):
    """Return the known type with this name (any case), creating it only when missing; None for a blank name."""
    name = (name or "").strip()
    if not name:
        return None
    return find_or_create_by_name(controller, "PlaceAssociationType", "type_name", name, known_types)
