"""Class for updating data in the database."""

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper
from src.db.db_helpers.track_dirty import mark_dirty_for_bulk_track_update, mark_dirty_for_entity_update, mark_dirty_for_rows
from src.foundation.logger_config import logger
from src.image.image_cleanup import IMAGE_PATH_COLUMNS, discard_replaced_image


class UpdateDB(BaseDBHelper):
    """Class for updating data in the database"""

    def update_entity(self, model_name: str, entity_id: int, **kwargs):
        """Update an existing entity in the database."""
        logger.debug(f"Updating {model_name} with ID {entity_id} with attributes: {kwargs}")
        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        # Attempt to determine the primary key column name for this model
        pk_cols = list(entity_class.__table__.primary_key.columns)
        pk_col = pk_cols[0].name if pk_cols else "id"

        conflict = self._find_unique_conflict(entity_class, pk_col, entity_id, kwargs)
        if conflict is not None:
            field, value, other_id = conflict
            logger.error(f"Cannot update {model_name} {entity_id}: '{field}' value {value!r} is already used by {model_name} {other_id}")
            return False

        # When an entity's managed picture column is being changed, remember
        # the path it currently holds so the now-unreferenced file on disk
        # can be unlinked once the new value is committed.
        image_col = IMAGE_PATH_COLUMNS.get(model_name)
        old_image_path = None
        if image_col and image_col[0] in kwargs:
            old_image_path = self.session.scalar(select(getattr(entity_class, image_col[0])).where(getattr(entity_class, pk_col) == entity_id))

        stmt = update(entity_class).where(getattr(entity_class, pk_col) == entity_id).values(**kwargs)

        try:
            mark_dirty_for_entity_update(self.session, model_name, [entity_id], kwargs)
            result = self.session.execute(stmt)
            self._commit()
            if result.rowcount == 0:
                logger.warning(f"{model_name} with ID {entity_id} not found; nothing updated")
                return False
            if old_image_path is not None:
                discard_replaced_image(self.session, model_name, old_image_path, kwargs[image_col[0]])
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating {model_name} with ID {entity_id}: {e}")
            self.session.rollback()
            return False

    def update_entity_by_filter(self, model_name: str, filters: dict, **kwargs):
        """Update every row matching `filters` -- for junction tables with a composite primary key (e.g. TrackArtistRole)."""
        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        try:
            query = self.session.query(entity_class)
            for attr, value in filters.items():
                if not hasattr(entity_class, attr):
                    logger.warning(f"{model_name} has no attribute '{attr}'")
                    return False
                query = query.filter(getattr(entity_class, attr) == value)

            mark_dirty_for_rows(self.session, model_name, query.all())
            updated = query.update(kwargs, synchronize_session="fetch")
            self._commit()
            if updated == 0:
                logger.warning(f"No {model_name} row(s) matched {filters}; nothing updated")
                return False
            logger.info(f"Updated {updated} {model_name} row(s) matching {filters}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating {model_name} matching {filters}: {e}")
            self.session.rollback()
            return False

    def update_entities(self, model_name: str, entity_ids: list, **kwargs):
        """Apply the same attribute changes to many entities in one statement."""
        if not entity_ids:
            return True

        logger.debug(f"Batch-updating {len(entity_ids)} {model_name} row(s) with attributes: {kwargs}")
        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        pk_cols = list(entity_class.__table__.primary_key.columns)
        pk_col = pk_cols[0].name if pk_cols else "id"
        pk_attr = getattr(entity_class, pk_col)

        conflict = self._find_unique_conflict_bulk(entity_class, pk_col, entity_ids, kwargs)
        if conflict is not None:
            field, value, other_id = conflict
            logger.error(f"Cannot batch-update {model_name}: '{field}' value {value!r} is already used by {model_name} {other_id}")
            return False

        stmt = update(entity_class).where(pk_attr.in_(entity_ids)).values(**kwargs)

        try:
            mark_dirty_for_entity_update(self.session, model_name, entity_ids, kwargs)
            result = self.session.execute(stmt)
            self._commit()
            if result.rowcount == 0:
                logger.warning(f"No {model_name} row(s) matched ids {entity_ids}; nothing updated")
                return False
            if result.rowcount < len(entity_ids):
                logger.warning(f"Batch-update of {model_name} matched only {result.rowcount}/{len(entity_ids)} of the requested ids")
            logger.info(f"Batch-updated {result.rowcount} {model_name} row(s)")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error batch-updating {model_name} ids {entity_ids}: {e}")
            self.session.rollback()
            return False

    def update_entities_bulk(self, model_name: str, updates: list):
        """Apply per-row differing attribute changes to many entities in one commit."""
        # Unlike `update_entities`, each row can be set to different values (e.g. giving every
        # track a distinct new file path). Uses SQLAlchemy's ORM bulk-UPDATE-by-primary-key
        # form to issue one executemany statement and a single commit for the whole batch,
        # instead of one commit per row.
        if not updates:
            return True

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        logger.debug(f"Bulk-updating {len(updates)} {model_name} row(s)")

        try:
            if model_name == "Track":
                mark_dirty_for_bulk_track_update(self.session, updates)
            self.session.execute(update(entity_class), updates)
            self._commit()
            logger.info(f"Bulk-updated {len(updates)} {model_name} row(s)")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error bulk-updating {model_name}: {e}")
            self.session.rollback()
            return False

    def update_entities_bulk_with_fallback(self, model_name: str, updates: list) -> tuple:
        """Bulk-update rows in one transaction, retrying one row at a time if the whole batch fails."""
        # Isolates a single bad row instead of sinking every row in the batch, so the caller
        # can report exactly which row(s) were dropped.
        if not updates:
            return 0, []

        if self.update_entities_bulk(model_name, updates):
            return len(updates), []

        logger.warning(f"Bulk-update of {len(updates)} {model_name} row(s) failed; retrying individually to isolate the bad row(s)")
        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return 0, updates

        pk_cols = list(entity_class.__table__.primary_key.columns)
        pk_col = pk_cols[0].name if pk_cols else "id"

        succeeded = 0
        failed = []
        for row in updates:
            entity_id = row[pk_col]
            kwargs = {k: v for k, v in row.items() if k != pk_col}
            if self.update_entity(model_name, entity_id, **kwargs):
                succeeded += 1
            else:
                failed.append(row)
        return succeeded, failed
