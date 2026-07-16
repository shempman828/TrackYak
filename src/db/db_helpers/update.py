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
