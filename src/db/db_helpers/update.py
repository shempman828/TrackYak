"""Class for updating data in the database."""

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper


class UpdateDB(BaseDBHelper):
    """Class for updating data in the database"""

    def update_entity(self, model_name: str, entity_id: int, **kwargs):
        """Update an existing entity in the database.

        Args:
            model_name (str): The class name of the entity (e.g., 'Artist', 'Playlist')
            entity_id (int): The ID of the entity to update.
            **kwargs: Arbitrary keyword arguments representing attribute values to update.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        logger.debug(
            f"Updating {model_name} with ID {entity_id} with attributes: {kwargs}"
        )
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
            logger.error(
                f"Cannot update {model_name} {entity_id}: '{field}' value "
                f"{value!r} is already used by {model_name} {other_id}"
            )
            return False

        stmt = (
            update(entity_class)
            .where(getattr(entity_class, pk_col) == entity_id)
            .values(**kwargs)
        )

        try:
            self.session.execute(stmt)
            self.session.commit()
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating {model_name} with ID {entity_id}: {e}")
            self.session.rollback()
            return False

    def update_entity_by_filter(self, model_name: str, filters: dict, **kwargs):
        """Update every row matching `filters` -- for junction tables with a
        composite primary key (e.g. TrackArtistRole), where update_entity's
        single entity_id can't identify one row.

        Args:
            model_name (str): The class name of the entity (e.g., 'TrackArtistRole').
            filters (dict): Column-value pairs identifying the row(s) to update.
            **kwargs: Attribute values to set on the matching row(s).

        Returns:
            bool: True if the update was successful, False otherwise.
        """
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

            updated = query.update(kwargs, synchronize_session="fetch")
            self.session.commit()
            logger.info(f"Updated {updated} {model_name} row(s) matching {filters}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating {model_name} matching {filters}: {e}")
            self.session.rollback()
            return False

    def update_entities(self, model_name: str, entity_ids: list, **kwargs):
        """Apply the same attribute changes to many entities in one statement.

        Args:
            model_name (str): The class name of the entity (e.g., 'Track').
            entity_ids (list[int]): IDs of the entities to update.
            **kwargs: Arbitrary keyword arguments representing attribute values to update.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        if not entity_ids:
            return True

        logger.debug(
            f"Batch-updating {len(entity_ids)} {model_name} row(s) "
            f"with attributes: {kwargs}"
        )
        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        pk_cols = list(entity_class.__table__.primary_key.columns)
        pk_col = pk_cols[0].name if pk_cols else "id"
        pk_attr = getattr(entity_class, pk_col)

        conflict = self._find_unique_conflict_bulk(
            entity_class, pk_col, entity_ids, kwargs
        )
        if conflict is not None:
            field, value, other_id = conflict
            logger.error(
                f"Cannot batch-update {model_name}: '{field}' value {value!r} "
                f"is already used by {model_name} {other_id}"
            )
            return False

        stmt = update(entity_class).where(pk_attr.in_(entity_ids)).values(**kwargs)

        try:
            self.session.execute(stmt)
            self.session.commit()
            logger.info(f"Batch-updated {len(entity_ids)} {model_name} row(s)")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error batch-updating {model_name} ids {entity_ids}: {e}")
            self.session.rollback()
            return False

    def update_entities_bulk(self, model_name: str, updates: list):
        """Apply per-row differing attribute changes to many entities in one commit.

        Unlike `update_entities`, each row can be set to different values (e.g.
        giving every track a distinct new file path). Uses SQLAlchemy's ORM
        bulk-UPDATE-by-primary-key form to issue one executemany statement and
        a single commit for the whole batch, instead of one commit per row.

        Args:
            model_name (str): The class name of the entity (e.g., 'Track').
            updates (list[dict]): Each dict must contain the primary key column
                plus the attribute values to set for that row.

        Returns:
            bool: True if the batch committed successfully, False otherwise.
        """
        if not updates:
            return True

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            return False

        logger.debug(f"Bulk-updating {len(updates)} {model_name} row(s)")

        try:
            self.session.execute(update(entity_class), updates)
            self.session.commit()
            logger.info(f"Bulk-updated {len(updates)} {model_name} row(s)")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error bulk-updating {model_name}: {e}")
            self.session.rollback()
            return False

    def update_entities_bulk_with_fallback(self, model_name: str, updates: list) -> tuple:
        """Bulk-update rows in one transaction; if the whole batch fails, retry
        one row at a time so a single bad row doesn't sink every other row in
        the batch, and the caller can report exactly which row(s) were dropped.

        Args:
            model_name (str): The class name of the entity (e.g., 'Track').
            updates (list[dict]): Each dict must contain the primary key column
                plus the attribute values to set for that row.

        Returns:
            tuple[int, list]: (number of rows successfully updated, updates
            that failed and were dropped).
        """
        if not updates:
            return 0, []

        if self.update_entities_bulk(model_name, updates):
            return len(updates), []

        logger.warning(
            f"Bulk-update of {len(updates)} {model_name} row(s) failed; "
            f"retrying individually to isolate the bad row(s)"
        )
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
