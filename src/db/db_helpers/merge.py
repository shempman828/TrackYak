"""Class for merging database entries."""

from sqlalchemy import select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import BaseDBHelper
from src.db.db_tables import (
    Album,
    Artist,
    ArtistAlias,
    Genre,
    GenreAlias,
    Mood,
    Publisher,
    PublisherAlias,
    Role,
)
from src.db.db_tables.award import AwardAssociation
from src.db.db_tables.place import PlaceAssociation

# Model registry — safer than globals()
_MERGE_MODEL_REGISTRY: dict = {
    "Artist": Artist,
    "Publisher": Publisher,
    "Genre": Genre,
    "Mood": Mood,
    "Role": Role,
    "Album": Album,
}

# For these entity types, merging automatically preserves the merged-away
# entity's name as an alias of the surviving entity, so the same "duplicate"
# name resolves to the canonical entity next time instead of being recreated
# (e.g. on import). Maps model name -> (alias model, name field, alias FK field).
_ALIAS_ON_MERGE_REGISTRY: dict = {
    "Artist": (ArtistAlias, "artist_name", "artist_id"),
    "Publisher": (PublisherAlias, "publisher_name", "publisher_id"),
    "Genre": (GenreAlias, "genre_name", "genre_id"),
}

# Tables that link to entities polymorphically via an (entity_type, entity_id)
# pair rather than a real foreign key. These are invisible to the FK-scanning
# loop in merge_entities() and must be migrated explicitly.
_POLYMORPHIC_ASSOCIATION_TABLES = [
    (PlaceAssociation.__table__, "entity_type", "entity_id"),
    (AwardAssociation.__table__, "entity_type", "entity_id"),
]


class MergeDB(BaseDBHelper):
    """Class for merging database entries."""

    def merge_entities(
        self,
        model_name: str,
        source_id: int,
        target_id: int,
        resolved_fields: dict | None = None,
    ):
        """Merge two entities of the same type across all relationship tables."""
        logger.debug(f"Merging {model_name} ID {source_id} -> {target_id}")

        entity_class = _MERGE_MODEL_REGISTRY.get(model_name)
        if entity_class is None:
            logger.error(f"Entity '{model_name}' not found in merge registry.")
            return False

        source_entity = self.session.get(entity_class, source_id)
        target_entity = self.session.get(entity_class, target_id)
        if not source_entity or not target_entity:
            logger.error(f"Source or target {model_name} not found.")
            return False

        try:
            pk_columns = [
                col.name for col in entity_class.__table__.primary_key.columns
            ]
            if not pk_columns:
                logger.error(f"No primary key found for {model_name}.")
                return False

            pk_column = pk_columns[0]
            metadata = entity_class.metadata
            updated_tables = set()
            skipped_tables = set()

            for table in metadata.tables.values():
                if table.name == entity_class.__table__.name:
                    continue

                for column in table.columns:
                    for fk in column.foreign_keys:
                        if (
                            fk.column.table.name == entity_class.__table__.name
                            and fk.column.name == pk_column
                        ):
                            logger.debug(
                                f"Found FK: {table.name}.{column.name} -> "
                                f"{entity_class.__table__.name}.{pk_column}"
                            )

                            update_stmt = (
                                update(table)
                                .where(column == source_id)
                                .values({column.name: target_id})
                            )

                            rowcount = self._safe_execute(update_stmt)
                            if rowcount > 0:
                                logger.info(
                                    f"Updated {rowcount} rows in "
                                    f"{table.name}.{column.name}"
                                )
                                updated_tables.add(table.name)
                            elif rowcount == -1:
                                # Bulk UPDATE hit a unique constraint on at least one
                                # row, so the whole statement rolled back. Fall back
                                # to a row-by-row pass so only rows that truly
                                # collide with an existing target row get dropped —
                                # the rest are still migrated to the target.
                                moved, dropped = self._migrate_fk_rows_individually(
                                    table, column, source_id, target_id
                                )
                                if moved:
                                    logger.info(
                                        f"Row-by-row updated {moved} rows in "
                                        f"{table.name}.{column.name}"
                                    )
                                    updated_tables.add(table.name)
                                if dropped:
                                    logger.info(
                                        f"Constraint on {table.name}.{column.name}: "
                                        f"deleted {dropped} duplicate source rows "
                                        f"(target already has them)."
                                    )
                                    skipped_tables.add(table.name)

            for assoc_table, type_col_name, id_col_name in (
                _POLYMORPHIC_ASSOCIATION_TABLES
            ):
                type_col = assoc_table.c[type_col_name]
                id_col = assoc_table.c[id_col_name]

                update_stmt = (
                    update(assoc_table)
                    .where(type_col == model_name, id_col == source_id)
                    .values({id_col_name: target_id})
                )

                rowcount = self._safe_execute(update_stmt)
                if rowcount > 0:
                    logger.info(
                        f"Updated {rowcount} rows in "
                        f"{assoc_table.name}.{id_col_name} (polymorphic {model_name})"
                    )
                    updated_tables.add(assoc_table.name)
                elif rowcount == -1:
                    moved, dropped = self._migrate_fk_rows_individually(
                        assoc_table,
                        id_col,
                        source_id,
                        target_id,
                        extra_where=(type_col == model_name),
                    )
                    if moved:
                        logger.info(
                            f"Row-by-row updated {moved} rows in "
                            f"{assoc_table.name}.{id_col_name} (polymorphic "
                            f"{model_name})"
                        )
                        updated_tables.add(assoc_table.name)
                    if dropped:
                        logger.info(
                            f"Constraint on {assoc_table.name}.{id_col_name}: "
                            f"deleted {dropped} duplicate source rows "
                            f"(target already has them)."
                        )
                        skipped_tables.add(assoc_table.name)

            self._preserve_alias_on_merge(model_name, source_entity, target_entity)

            # Delete via ORM so cascade rules are respected
            self.session.delete(source_entity)
            self.session.flush()
            # Apply resolved field values now that the source is scheduled for deletion.
            if resolved_fields:
                for field, value in resolved_fields.items():
                    if not hasattr(target_entity, field):
                        continue

                    # Skip unchanged values
                    if getattr(target_entity, field) == value:
                        continue

                    # The source row is already flushed for deletion, but a
                    # *third*, unrelated row could still hold this value —
                    # check for that before assigning so a collision doesn't
                    # blow up the commit below and force a rollback of the
                    # whole merge.
                    conflict = self._find_unique_conflict(
                        entity_class, pk_column, target_id, {field: value}
                    )
                    if conflict is not None:
                        _, conflict_value, other_id = conflict
                        logger.error(
                            f"Cannot merge {model_name} {source_id} -> {target_id}: "
                            f"resolved '{field}' value {conflict_value!r} is already "
                            f"used by {model_name} {other_id}"
                        )
                        self.session.rollback()
                        return False

                    setattr(target_entity, field, value)
            self.session.commit()

            logger.info(
                f"Merge complete: {model_name} {source_id} -> {target_id}. "
                f"Updated tables: {sorted(updated_tables)}. "
                f"Constraint-skipped tables: {sorted(skipped_tables)}."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error merging {model_name}: {e}")
            self.session.rollback()
            return False

    def _preserve_alias_on_merge(self, model_name, source_entity, target_entity):
        """For entity types in `_ALIAS_ON_MERGE_REGISTRY`, save the merged-away
        entity's name as an alias of the surviving entity, so that name
        resolves back to the same entity instead of creating a duplicate
        later (e.g. on import).
        """
        alias_info = _ALIAS_ON_MERGE_REGISTRY.get(model_name)
        if not alias_info:
            return

        alias_class, name_field, fk_field = alias_info
        source_name = getattr(source_entity, name_field)
        target_name = getattr(target_entity, name_field)
        if not source_name or source_name == target_name:
            return

        # `fk_field` (e.g. "publisher_id") is both the alias table's FK
        # column and the target entity's own primary-key attribute name.
        target_id = getattr(target_entity, fk_field)

        existing_alias = self.session.scalar(
            select(alias_class).where(alias_class.alias_name == source_name)
        )
        if existing_alias is not None:
            if getattr(existing_alias, fk_field) != target_id:
                setattr(existing_alias, fk_field, target_id)
            return

        self.session.add(
            alias_class(alias_name=source_name, **{fk_field: target_id})
        )
        logger.info(
            f"Preserved '{source_name}' as an alias of {model_name} {target_id} "
            f"after merge."
        )

    def _safe_execute(self, stmt) -> int:
        """
        Execute a statement inside a savepoint.
        Returns rowcount on success, -1 if a unique/integrity constraint fired.
        Any other SQLAlchemyError is re-raised.
        """
        try:
            with self.session.begin_nested():
                result = self.session.execute(stmt)
                return result.rowcount
        except IntegrityError:
            logger.debug("Skipping statement due to unique constraint violation.")
            return -1

    def _migrate_fk_rows_individually(
        self, table, fk_column, source_id, target_id, extra_where=None
    ):
        """Move rows referencing source_id to target_id one row at a time.

        Used as a fallback when a bulk UPDATE fails because at least one row
        would collide with a unique constraint (the target already has an
        equivalent row). Handling rows individually ensures non-conflicting
        rows are still migrated instead of being dropped along with the
        genuine duplicates.

        `extra_where` narrows the row selection further (e.g. matching an
        `entity_type` discriminator on a polymorphic association table).
        """
        pk_columns = list(table.primary_key.columns)
        select_stmt = select(*pk_columns).where(fk_column == source_id)
        if extra_where is not None:
            select_stmt = select_stmt.where(extra_where)
        rows = self.session.execute(select_stmt).fetchall()

        moved = 0
        dropped = 0
        for row in rows:
            row_filter = [col == val for col, val in zip(pk_columns, row)]
            update_stmt = (
                update(table).where(*row_filter).values({fk_column.name: target_id})
            )
            rowcount = self._safe_execute(update_stmt)
            if rowcount > 0:
                moved += 1
            elif rowcount == -1:
                delete_stmt = sql_delete(table).where(*row_filter)
                self._safe_execute(delete_stmt)
                dropped += 1

        return moved, dropped
