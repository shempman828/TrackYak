"""
MusicDatabase: engine/session setup plus schema and integrity verification.
"""

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.db_tables.base import Base
from src.logger_config import logger


class MusicDatabase:
    def __init__(self, db_path: str = "sqlite:///music_library.db") -> None:
        try:
            self.engine = create_engine(db_path, echo=False)
            self.Session = sessionmaker(bind=self.engine)
            self._initialize_database()
            self._verify_integrity()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _initialize_database(self):
        """Creates the database schema if it doesn't already exist."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def _verify_integrity(self):
        """Check that all expected tables exist, then verify columns match the ORM models.

        - Missing tables are recreated automatically via create_all().
        - Missing columns are logged as warnings. They won't crash the app on startup,
          but any query touching that column will fail until it's added manually via
          an ALTER TABLE migration (SQLite doesn't support automatic column addition).
        """
        try:
            inspector = inspect(self.engine)
            existing_tables = set(inspector.get_table_names())

            # ── Step 1: Table check ──────────────────────────────────────────
            expected_tables = {
                "albums",
                "tracks",
                "artists",
                "genres",
                "moods",
                "publishers",
                "places",
                "place_associations",
                "group_membership",
                "track_artist_roles",
                "playlists",
                "awards",
                "album_role_association",
                "playlist_tracks",
                "discs",
                "roles",
                "album_publisher",
                "track_genres",
                "mood_track_association",
                "award_associations",
                "artist_influences",
                "smart_playlists",
                "smart_playlist_criteria",
                "artist_alias",
                "samples",
                "album_virtual_tracks",
                "track_usages",
            }

            missing_tables = expected_tables - existing_tables
            if missing_tables:
                logger.warning(f"Missing tables: {missing_tables}. Recreating...")
                Base.metadata.create_all(self.engine)
                logger.info("Database schema recreated successfully.")
                # Re-inspect after recreation so column checks see the new tables
                inspector = inspect(self.engine)
                existing_tables = set(inspector.get_table_names())
            else:
                logger.info("Table integrity check passed.")

            # ── Step 2: Column check ─────────────────────────────────────────
            # For every ORM model, compare the columns defined in Python against
            # the columns that actually exist in the database file.
            missing_columns = []
            for table_name, table in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue  # Already handled above

                existing_columns = {
                    col["name"] for col in inspector.get_columns(table_name)
                }
                for column in table.columns:
                    if column.name not in existing_columns:
                        missing_columns.append(f"{table_name}.{column.name}")

            if missing_columns:
                logger.warning(
                    f"The following columns exist in the ORM but are missing from the "
                    f"database file — queries using them will fail until a migration is "
                    f"run: {sorted(missing_columns)}"
                )
            else:
                logger.info("Column integrity check passed.")

        except Exception as e:
            logger.error(f"Integrity check failed: {e}")
            raise
