"""Class for adding data to the database."""

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper


class AddToDB(BaseDBHelper):
    """Class for adding data to the database"""

    def add_entity(self, model_name: str, **kwargs):
        logger.debug(
            f"Adding new entity of type: {model_name} with attributes: {kwargs}"
        )

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return None

        new_entity = entity_class(**kwargs)
        self.session.add(new_entity)

        try:
            self.session.commit()

            # Safe refresh with error handling
            try:
                self.session.refresh(new_entity)
                logger.debug(f"New entity added and refreshed: {new_entity}")
            except SQLAlchemyError as refresh_error:
                logger.warning(
                    f"Could not refresh entity {model_name} after commit: {refresh_error}. "
                    f"Entity was still added successfully."
                )
                # The entity was committed, so we return it even if refresh fails

            return new_entity

        except Exception as e:
            self.session.rollback()
            logger.error(f"Failed to add entity: {e}")
            return None

    def add_entity_link(self, link_type: str, **kwargs):
        """Add a new link entity to the database.

        Args:
            link_type (str): The class name of the link entity (e.g., 'TrackArtistRole', 'AlbumArtistAssociation')
            **kwargs: Arbitrary keyword arguments representing attribute values.

        Returns:
            object: The newly created link entity instance.
        """
        logger.debug(
            f"Adding new link entity of type: {link_type} with attributes: {kwargs}"
        )
        try:
            link_class = MODEL_REGISTRY[link_type]
        except KeyError:
            return None

        new_link = link_class(**kwargs)
        self.session.add(new_link)
        self.session.commit()
        return new_link
