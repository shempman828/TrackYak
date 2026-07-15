"""Shared model registry and base class used by all db_helpers modules."""

import inspect

from sqlalchemy import select

import src.db.db_tables

# Maps every ORM class name in src.db.db_tables to its class object, so the
# rest of this package can look entities up by string name (e.g. "Track")
# without each module needing its own copy of globals().
MODEL_REGISTRY: dict = {
    name: obj
    for name, obj in inspect.getmembers(src.db.db_tables)
    if inspect.isclass(obj)
}


class BaseDBHelper:
    """Base class with common database operations"""

    def __init__(self, session):
        """Initialize with a database session."""
        self.session = session

    def _find_unique_conflict(self, entity_class, pk_col, entity_id, values: dict):
        """Check whether any of ``values`` would collide with a unique column
        on some *other* row of ``entity_class``.

        Returns a ``(field_name, value, conflicting_id)`` tuple for the first
        collision found, or ``None`` if there is no conflict. Checking this
        up front lets callers avoid ever issuing a write that the database
        would reject with a UNIQUE constraint failure.
        """
        for column in entity_class.__table__.columns:
            if not column.unique or column.name not in values:
                continue

            value = values[column.name]
            conflict = self.session.scalar(
                select(entity_class).where(
                    column == value,
                    getattr(entity_class, pk_col) != entity_id,
                )
            )
            if conflict is not None:
                return column.name, value, getattr(conflict, pk_col)

        return None
