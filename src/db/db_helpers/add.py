"""Class for adding data to the database."""

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper


class AddToDB(BaseDBHelper):
    """Class for adding data to the database"""

    def add_entity(self, model_name: str, commit: bool = True, **kwargs):
        """Create and persist a new entity.

        Args:
            model_name: The class name of the entity (e.g., 'Track').
            commit: When True (the default, used by every non-import call
                site), each call is its own transaction: added, committed
                immediately, and rolled back on failure.
                When False, the row is flushed (so its primary key and
                server-side defaults are populated) but the transaction is
                left open and exceptions propagate instead of being caught
                here — for a caller (e.g. the library importer) that's
                batching several related inserts into a single
                all-or-nothing transaction it commits/rolls back itself.
        """
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

        if not commit:
            self.session.flush()
            self.session.refresh(new_entity)
            return new_entity

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

        except SQLAlchemyError as e:
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

    def add_entities(self, model_name: str, rows: list):
        """Add many entities of the same type in a single transaction.

        Args:
            model_name (str): The class name of the entity (e.g., 'TrackArtistRole').
            rows (list[dict]): One kwargs-dict of attribute values per row to create.

        Returns:
            list: The newly created entity instances (rows that already existed,
            for entities keyed entirely by primary-key columns, are skipped
            rather than raising -- a single duplicate would otherwise roll
            back the whole batch).
        """
        if not rows:
            return []

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return []

        pk_cols = [c.name for c in entity_class.__table__.primary_key.columns]
        rows_to_add = rows
        if pk_cols and all(col in row for row in rows for col in pk_cols):
            existing = set(
                self.session.query(
                    *[getattr(entity_class, c) for c in pk_cols]
                ).all()
            )
            rows_to_add = [
                row
                for row in rows
                if tuple(row[c] for c in pk_cols) not in existing
            ]
            skipped = len(rows) - len(rows_to_add)
            if skipped:
                logger.debug(f"Skipped {skipped} duplicate {model_name} row(s)")

        if not rows_to_add:
            return []

        logger.debug(f"Batch-adding {len(rows_to_add)} {model_name} row(s)")
        new_entities = [entity_class(**row) for row in rows_to_add]
        self.session.add_all(new_entities)

        try:
            self.session.commit()
            logger.info(f"Batch-added {len(new_entities)} {model_name} row(s)")
            return new_entities
        except SQLAlchemyError as e:
            self.session.rollback()
            logger.error(f"Failed to batch-add {model_name} entities: {e}")
            return []
