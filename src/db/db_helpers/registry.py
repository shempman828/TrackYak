"""Shared model registry and base class used by all db_helpers modules."""

import inspect

from sqlalchemy import select

from src.core.logger_config import logger
import src.db.db_tables

# Maps every ORM class name in src.db.db_tables to its class object, so the
# rest of this package can look entities up by string name (e.g. "Track")
# without each module needing its own copy of globals().
MODEL_REGISTRY: dict = {
    name: obj for name, obj in inspect.getmembers(src.db.db_tables) if inspect.isclass(obj)
}


class BaseDBHelper:
    """Base class with common database operations"""

    def __init__(self, session):
        """Initialize with a database session."""
        self.session = session

    def _commit(self):
        """Commit a write and expire every cached attribute in the session.

        expire_on_commit=False (src/db/db_engine.py) means a plain commit()
        never invalidates a relationship/attribute already loaded on some
        other long-lived object elsewhere in the app -- see project memory
        on "stale relationship cache" for the recurring bug shape this
        caused (genres, disc, album credits, playlists, ...). expire_all()
        issues no query itself; it just marks every loaded object's
        attributes stale so the next read anywhere reloads for real. Only
        write helpers (add/delete/update/split/merge) should call this --
        get.py's read-path commits must NOT expire_all, or every fetch would
        invalidate the rows it just returned.
        """
        self.session.commit()
        self.session.expire_all()

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
            if value is None:
                continue

            conflict = self.session.scalar(
                select(entity_class).where(
                    column == value, getattr(entity_class, pk_col) != entity_id
                )
            )
            if conflict is not None:
                logger.debug(
                    f"Unique conflict on {entity_class.__name__}.{column.name}={value!r} "
                    f"(conflicts with id={getattr(conflict, pk_col)})"
                )
                return column.name, value, getattr(conflict, pk_col)

        return None

    def _find_unique_conflict_bulk(self, entity_class, pk_col, entity_ids: list, values: dict):
        """Same as ``_find_unique_conflict``, but for a batch update covering
        several rows at once: a collision only counts if it belongs to a row
        *outside* ``entity_ids`` (rows being updated together are allowed to
        end up sharing the same new value only if that value isn't unique).
        """
        for column in entity_class.__table__.columns:
            if not column.unique or column.name not in values:
                continue

            value = values[column.name]
            if value is None:
                continue

            conflict = self.session.scalar(
                select(entity_class).where(
                    column == value, getattr(entity_class, pk_col).notin_(entity_ids)
                )
            )
            if conflict is not None:
                logger.debug(
                    f"Unique conflict (bulk) on {entity_class.__name__}.{column.name}={value!r} "
                    f"(conflicts with id={getattr(conflict, pk_col)})"
                )
                return column.name, value, getattr(conflict, pk_col)

        return None
