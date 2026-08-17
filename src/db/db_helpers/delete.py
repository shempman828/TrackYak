"""Minimal class for deleting database entities and associated files."""

import os

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper
from src.db.db_helpers.track_dirty import mark_dirty_for_rows


class DeleteDB(BaseDBHelper):
    """Minimal class for deleting database entities and associated files."""

    def delete_entity(
        self,
        model_name: str,
        entity_id: int | None = None,
        entity_ids: list | None = None,
        **filters,
    ):
        """
        Delete one or many database entities.

        Three ways to call this:

            # Single item by primary key (original behaviour, unchanged)
            delete_entity("Track", entity_id=42)

            # Many items in one query -- new batch path
            delete_entity("Track", entity_ids=[1, 2, 3, 99])

            # Filter-based deletion (original behaviour, unchanged); any
            # filter value that is a list/tuple/set is matched with IN(...)
            delete_entity("Track", track_name="Unknown")
            delete_entity("TrackArtistRole", track_id=[1, 2, 3], artist_id=5, role_id=2)
        """
        entity_class = MODEL_REGISTRY.get(model_name)
        if not entity_class:
            logger.error(f"Entity type '{model_name}' not found")
            return False

        try:
            # ------------------------------------------------------------------
            # BATCH path -- new: delete many rows in a single WHERE id IN query
            # ------------------------------------------------------------------
            if entity_ids is not None:
                if not entity_ids:
                    logger.warning("delete_entity called with an empty entity_ids list")
                    return True  # Nothing to do -- not an error

                pk_cols = list(entity_class.__table__.primary_key.columns)
                pk_col = pk_cols[0].name if pk_cols else "id"

                to_delete = self.session.query(entity_class).filter(
                    getattr(entity_class, pk_col).in_(entity_ids)
                )
                mark_dirty_for_rows(self.session, model_name, to_delete.all())
                to_delete.delete(synchronize_session="fetch")
                # "fetch" tells SQLAlchemy to load the objects first so that
                # cascade rules (e.g. deleting related join rows) fire correctly.
                self.session.commit()
                logger.info(
                    f"Batch-deleted {len(entity_ids)} {model_name} row(s) "
                    f"(ids={entity_ids})"
                )
                return True

            # ------------------------------------------------------------------
            # SINGLE item path -- original behaviour, unchanged
            # ------------------------------------------------------------------
            elif entity_id is not None:
                entity = self.session.get(entity_class, entity_id)
                if not entity:
                    logger.warning(f"{model_name} with ID {entity_id} not found")
                    return False
                mark_dirty_for_rows(self.session, model_name, [entity])
                self.session.delete(entity)
                self.session.commit()
                logger.info(f"Deleted {model_name} with ID {entity_id}")
                return True

            # ------------------------------------------------------------------
            # FILTER path -- original behaviour, unchanged
            # ------------------------------------------------------------------
            elif filters:
                query = self.session.query(entity_class)
                for attr, value in filters.items():
                    if not hasattr(entity_class, attr):
                        logger.warning(f"{model_name} has no attribute '{attr}'")
                        return False
                    column = getattr(entity_class, attr)
                    if isinstance(value, (list, tuple, set)):
                        query = query.filter(column.in_(value))
                    else:
                        query = query.filter(column == value)
                entities = query.all()
                mark_dirty_for_rows(self.session, model_name, entities)
                for entity in entities:
                    self.session.delete(entity)
                self.session.commit()
                logger.info(
                    f"Deleted {len(entities)} {model_name} entities matching {filters}"
                )
                return True

            else:
                logger.error(
                    "Either entity_id, entity_ids, or filters must be provided"
                )
                return False

        except SQLAlchemyError as e:
            logger.error(f"Error deleting {model_name}: {e}")
            self.session.rollback()
            return False

    def delete_file(
        self,
        file_path: str | None = None,
        model_name: str | None = None,
        entity_id: int | None = None,
        **filters,
    ):
        """Delete a file from disk after deleting its database entry."""
        db_deleted = True
        if model_name:
            db_deleted = self.delete_entity(model_name, entity_id=entity_id, **filters)

        file_deleted = True
        if file_path:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted file: {file_path}")
                else:
                    logger.warning(f"File not found: {file_path}")
                    file_deleted = False
            except PermissionError as e:
                logger.error(f"Permission denied deleting file {file_path}: {e}")
                file_deleted = False
            except OSError as e:
                logger.error(f"Error deleting file {file_path}: {e}")
                file_deleted = False

        return db_deleted and file_deleted
