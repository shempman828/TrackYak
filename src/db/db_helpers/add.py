"""Class for adding data to the database."""

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper
from src.db.db_helpers.track_dirty import mark_dirty_for_new_rows


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
            mark_dirty_for_new_rows(self.session, model_name, [kwargs])
            self.session.flush()
            self.session.refresh(new_entity)
            return new_entity

        try:
            with self.session.no_autoflush:
                mark_dirty_for_new_rows(self.session, model_name, [kwargs])
            self._commit()

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
        mark_dirty_for_new_rows(self.session, link_type, [kwargs])
        self._commit()
        return new_link

    def _split_existing_rows(self, entity_class, rows: list) -> tuple:
        """Split rows into (new, already-existing), when every row supplies
        its full primary key -- true for composite-PK association tables
        (e.g. TrackGenre's track_id+genre_id) where the "row" *is* the key.

        Returns (rows, []) unchanged when rows don't carry a full PK (e.g.
        entities with a surrogate id column), since there's nothing to
        dedupe against without issuing a row-by-row lookup.
        """
        pk_cols = [c.name for c in entity_class.__table__.primary_key.columns]
        if not pk_cols or not all(col in row for row in rows for col in pk_cols):
            return rows, []

        existing = set(
            self.session.query(*[getattr(entity_class, c) for c in pk_cols]).all()
        )
        new_rows = [
            row for row in rows if tuple(row[c] for c in pk_cols) not in existing
        ]
        existing_rows = [
            row for row in rows if tuple(row[c] for c in pk_cols) in existing
        ]
        return new_rows, existing_rows

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

        rows_to_add, existing_rows = self._split_existing_rows(entity_class, rows)
        if existing_rows:
            logger.debug(f"Skipped {len(existing_rows)} duplicate {model_name} row(s)")

        if not rows_to_add:
            return []

        logger.debug(f"Batch-adding {len(rows_to_add)} {model_name} row(s)")
        new_entities = [entity_class(**row) for row in rows_to_add]
        self.session.add_all(new_entities)
        with self.session.no_autoflush:
            mark_dirty_for_new_rows(self.session, model_name, rows_to_add)

        try:
            self._commit()
            logger.info(f"Batch-added {len(new_entities)} {model_name} row(s)")
            return new_entities
        except SQLAlchemyError as e:
            self.session.rollback()
            logger.error(f"Failed to batch-add {model_name} entities: {e}")
            return []

    def add_entities_with_fallback(self, model_name: str, rows: list) -> tuple:
        """Batch-add rows in one transaction; if the whole batch fails, retry
        one row at a time so a single bad row doesn't sink every other row in
        the batch, and the caller can report exactly which row(s) were dropped.

        Rows that already exist (for composite-PK association tables, where
        the row's own attributes are its primary key -- e.g. TrackGenre) are
        filtered out up front and never attempted or reported as failed:
        re-tagging a track with a genre it already has is a no-op, not an
        error, and must not surface a "some tracks not updated" warning.

        Args:
            model_name (str): The class name of the entity (e.g., 'TrackArtistRole').
            rows (list[dict]): One kwargs-dict of attribute values per row to create.

        Returns:
            tuple[list, list]: (entities successfully added, rows that failed
            and were dropped). A short result from `add_entities` here always
            means the commit failed, not a dedup skip, since duplicates were
            already removed from `rows_to_add` before it was called.
        """
        if not rows:
            return [], []

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return [], rows

        rows_to_add, existing_rows = self._split_existing_rows(entity_class, rows)
        if existing_rows:
            logger.debug(
                f"Skipped {len(existing_rows)} duplicate {model_name} row(s) "
                "already present"
            )
        if not rows_to_add:
            return [], []

        entities = self.add_entities(model_name, rows_to_add)
        if len(entities) == len(rows_to_add):
            return entities, []

        logger.warning(
            f"Batch-add of {len(rows_to_add)} {model_name} row(s) failed; "
            f"retrying individually to isolate the bad row(s)"
        )
        succeeded = []
        failed = []
        for row in rows_to_add:
            entity = self.add_entity(model_name, **row)
            if entity is not None:
                succeeded.append(entity)
            else:
                failed.append(row)
        return succeeded, failed
