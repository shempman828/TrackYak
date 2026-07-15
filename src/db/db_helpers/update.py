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
