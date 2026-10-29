"""MusicDatabase: engine/session setup plus schema and integrity verification."""

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.db.db_engine import engine as _shared_engine
from src.db.db_tables import migrations
from src.db.db_tables.base import Base
from src.foundation.logger_config import logger

_DEFAULT_DB_PATH = "sqlite:///music_library.db"


class MusicDatabase:
    """Opens the library database, creates the schema, and runs startup migrations."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        try:
            # The default path reuses the app-wide engine so there is one pool per
            # SQLite file; any other path (e.g. tests) gets its own engine.
            self.engine = _shared_engine if db_path == _DEFAULT_DB_PATH else create_engine(db_path, echo=False)
            self.Session = sessionmaker(bind=self.engine)
            self._initialize_database()
            self._verify_integrity()
        except SQLAlchemyError as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _initialize_database(self):
        """Create the database schema if it does not already exist."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables initialized successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def _verify_integrity(self):
        """Bring an existing database file up to the ORM schema: tables, columns, indexes, and backfills."""
        try:
            inspector = inspect(self.engine)
            existing_tables = set(inspector.get_table_names())

            # ── Step 1: Table check ──────────────────────────────────────────
            missing_tables = set(Base.metadata.tables) - existing_tables
            if missing_tables:
                logger.warning(f"Missing tables: {missing_tables}. Recreating...")
                Base.metadata.create_all(self.engine)
                logger.info("Database schema recreated successfully.")
                inspector = inspect(self.engine)
                existing_tables = set(inspector.get_table_names())
            else:
                logger.info("Table integrity check passed.")

            # ── Step 1b: One-off column renames ──────────────────────────────
            # Run before Step 2, which would otherwise add an empty column and orphan the old data.
            if "tracks" in existing_tables:
                migrations.rename_column_if_needed(self.engine, "tracks", "fidelity_score", "audiophile_score")
            for table_name in ("albums", "artists", "tracks", "publishers"):
                if table_name in existing_tables:
                    migrations.rename_column_if_needed(self.engine, table_name, "is_fixed", "first_pass")

            if "artists" in existing_tables:
                migrations.drop_artist_name_unique_constraint_if_needed(self.engine)

            # ── Step 2: Column check ─────────────────────────────────────────
            # Safe additive columns are added via ALTER TABLE; the rest are only logged.
            missing_columns = []
            for table_name, table in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue

                existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
                for column in table.columns:
                    if column.name in existing_columns:
                        continue
                    if migrations.try_add_column(self.engine, table_name, column):
                        logger.info(f"Added missing column {table_name}.{column.name} via ALTER TABLE.")
                    else:
                        missing_columns.append(f"{table_name}.{column.name}")

            if missing_columns:
                logger.warning(f"The following columns exist in the ORM but are missing from the database file — queries using them will fail until a migration is run: {sorted(missing_columns)}")
            else:
                logger.info("Column integrity check passed.")

            # ── Step 3: Index check ──────────────────────────────────────────
            # create_all() never adds an index to a table that already exists.
            migrations.drop_retired_indexes(self.engine)
            if "place_associations" in existing_tables:
                migrations.dedupe_place_associations(self.engine)
            if "album_virtual_tracks" in existing_tables:
                migrations.dedupe_album_virtual_tracks(self.engine)
            migrations.create_missing_indexes(self.engine, existing_tables)

            # ── Step 4: One-off data backfill ────────────────────────────────
            if "albums" in existing_tables and "tracks" in existing_tables:
                migrations.backfill_album_gain_peak(self.engine)
            if "albums" in existing_tables:
                migrations.normalize_release_type_casing(self.engine)

            # ── Step 5: FTS5 virtual table setup ─────────────────────────────
            if "chart_entries" in existing_tables:
                migrations.ensure_chart_entries_fts(self.engine)

        except SQLAlchemyError as e:
            logger.error(f"Integrity check failed: {e}")
            raise
