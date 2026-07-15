"""Class for merging database entries."""

from sqlalchemy import select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import BaseDBHelper
from src.db.db_tables import Album, Artist, Genre, Mood, Publisher, Role

# Model registry — safer than globals()
_MERGE_MODEL_REGISTRY: dict = {
    "Artist": Artist,
    "Publisher": Publisher,
    "Genre": Genre,
    "Mood": Mood,
    "Role": Role,
    "Album": Album,
}


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

    def _migrate_fk_rows_individually(self, table, fk_column, source_id, target_id):
        """Move rows referencing source_id to target_id one row at a time.

        Used as a fallback when a bulk UPDATE fails because at least one row
        would collide with a unique constraint (the target already has an
        equivalent row). Handling rows individually ensures non-conflicting
        rows are still migrated instead of being dropped along with the
        genuine duplicates.
        """
        pk_columns = list(table.primary_key.columns)
        select_stmt = select(*pk_columns).where(fk_column == source_id)
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
