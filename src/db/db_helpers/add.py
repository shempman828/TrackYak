"""Class for adding data to the database."""

from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper
from src.db.db_helpers.track_dirty import mark_dirty_for_new_rows
from src.foundation.logger_config import logger


class AddToDB(BaseDBHelper):
    """Class for adding data to the database"""

    def add_entity(self, model_name: str, commit: bool = True, **kwargs):
        """Create and persist a new entity."""
        logger.debug(f"Adding new entity of type: {model_name} with attributes: {kwargs}")

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return None

        new_entity = entity_class(**kwargs)
        self.session.add(new_entity)

        if not commit:
            # commit=False: flush only (populates the PK/server-side defaults) and leave
            # the transaction open, exceptions propagating uncaught -- for a caller (e.g.
            # the library importer) batching several related inserts into a single
            # all-or-nothing transaction it commits/rolls back itself.
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
                logger.warning(f"Could not refresh entity {model_name} after commit: {refresh_error}. Entity was still added successfully.")
                # The entity was committed, so we return it even if refresh fails

            return new_entity

        except SQLAlchemyError as e:
            self.session.rollback()
            logger.error(f"Failed to add entity: {e}")
            return None

    def add_entity_link(self, link_type: str, **kwargs):
        """Add a new link entity to the database (e.g. 'TrackArtistRole', 'AlbumArtistAssociation')."""
        logger.debug(f"Adding new link entity of type: {link_type} with attributes: {kwargs}")
        try:
            link_class = MODEL_REGISTRY[link_type]
        except KeyError:
            return None

        new_link = link_class(**kwargs)
        self.session.add(new_link)

        try:
            mark_dirty_for_new_rows(self.session, link_type, [kwargs])
            self._commit()
            return new_link
        except SQLAlchemyError as e:
            self.session.rollback()
            logger.error(f"Failed to add {link_type} link: {e}")
            return None

    def _split_existing_rows(self, entity_class, rows: list) -> tuple:
        """Split rows into (new, already-existing), when every row supplies its full primary key."""
        # True for composite-PK association tables (e.g. TrackGenre's track_id+genre_id)
        # where the "row" *is* the key. Returns (rows, []) unchanged when rows don't carry
        # a full PK (e.g. entities with a surrogate id column), since there's nothing to
        # dedupe against without issuing a row-by-row lookup.
        pk_cols = [c.name for c in entity_class.__table__.primary_key.columns]
        if not pk_cols or not all(col in row for row in rows for col in pk_cols):
            return rows, []

        # Scoped to just the incoming rows' key values rather than loading every
        # existing PK for the whole table, which would be wasteful for a large
        # association table (e.g. TrackGenre) when checking a small batch.
        pk_attrs = [getattr(entity_class, c) for c in pk_cols]
        row_keys = [tuple(row[c] for c in pk_cols) for row in rows]
        existing_query = select(pk_attrs[0]).where(pk_attrs[0].in_(k[0] for k in row_keys)) if len(pk_attrs) == 1 else select(*pk_attrs).where(tuple_(*pk_attrs).in_(row_keys))
        existing = set(self.session.execute(existing_query).all())

        new_rows = [row for row, key in zip(rows, row_keys, strict=True) if key not in existing]
        existing_rows = [row for row, key in zip(rows, row_keys, strict=True) if key in existing]
        return new_rows, existing_rows

    def add_entities(self, model_name: str, rows: list):
        """Add many entities of the same type in a single transaction."""
        # Rows that already exist (for entities keyed entirely by primary-key
        # columns) are skipped rather than raising -- a single duplicate would
        # otherwise roll back the whole batch.
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
        """Batch-add rows in one transaction, retrying one row at a time if the whole batch fails."""
        # Isolates a single bad row instead of sinking every row in the batch, so the caller
        # can report exactly which row(s) were dropped. Rows that already exist (for
        # composite-PK association tables, e.g. TrackGenre) are filtered out up front and
        # never attempted or reported as failed: re-tagging a track with a genre it already
        # has is a no-op, not an error.
        if not rows:
            return [], []

        try:
            entity_class = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return [], rows

        rows_to_add, existing_rows = self._split_existing_rows(entity_class, rows)
        if existing_rows:
            logger.debug(f"Skipped {len(existing_rows)} duplicate {model_name} row(s) already present")
        if not rows_to_add:
            return [], []

        entities = self.add_entities(model_name, rows_to_add)
        if len(entities) == len(rows_to_add):
            return entities, []

        logger.warning(f"Batch-add of {len(rows_to_add)} {model_name} row(s) failed; retrying individually to isolate the bad row(s)")
        succeeded = []
        failed = []
        for row in rows_to_add:
            entity = self.add_entity(model_name, **row)
            if entity is not None:
                succeeded.append(entity)
            else:
                failed.append(row)
        return succeeded, failed
