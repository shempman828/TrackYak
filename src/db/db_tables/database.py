"""
MusicDatabase: engine/session setup plus schema and integrity verification.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.db.db_engine import engine as _shared_engine
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.core.logger_config import logger
from src.statistics.album_gain_peak import compute_album_gain_peak

_DEFAULT_DB_PATH = "sqlite:///music_library.db"


class MusicDatabase:
    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        try:
            # Reuse the app-wide shared engine for the default path so this
            # doesn't open a second, uncoordinated connection pool against the
            # same SQLite file as MusicController/db_helpers. A non-default
            # path (e.g. for tests) still gets its own dedicated engine.
            self.engine = (
                _shared_engine if db_path == _DEFAULT_DB_PATH else create_engine(db_path, echo=False)
            )
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
          an ALTER TABLE migration (SQLite can't express anything beyond a simple
          additive column this way).
        """
        try:
            inspector = inspect(self.engine)
            existing_tables = set(inspector.get_table_names())

            # ── Step 1: Table check ──────────────────────────────────────────
            expected_tables = {
                "albums",
                "tracks",
                "artists",
                "artist_types",
                "artist_type_associations",
                "genres",
                "religions",
                "moods",
                "publishers",
                "places",
                "place_associations",
                "place_association_types",
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
                "publisher_alias",
                "genre_alias",
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

            # ── Step 1b: One-off column renames ──────────────────────────────
            # Renamed columns aren't "missing" from the ORM's perspective, so
            # the additive Step 2 check below would just add a fresh, empty
            # column and silently orphan the old one (and its data). Handle
            # known renames explicitly first, preserving existing values.
            if "tracks" in existing_tables:
                self._rename_column_if_needed(
                    "tracks", "fidelity_score", "audiophile_score"
                )

            # ── Step 2: Column check ─────────────────────────────────────────
            # For every ORM model, compare the columns defined in Python against
            # the columns that actually exist in the database file. Simple
            # additive columns (nullable, or NOT NULL with a constant default)
            # are added automatically via ALTER TABLE; anything else is just
            # logged, since SQLite's ADD COLUMN can't express it safely.
            missing_columns = []
            for table_name, table in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue  # Already handled above

                existing_columns = {
                    col["name"] for col in inspector.get_columns(table_name)
                }
                for column in table.columns:
                    if column.name in existing_columns:
                        continue
                    if self._try_add_column(table_name, column):
                        logger.info(
                            f"Added missing column {table_name}.{column.name} "
                            f"via ALTER TABLE."
                        )
                    else:
                        missing_columns.append(f"{table_name}.{column.name}")

            if missing_columns:
                logger.warning(
                    f"The following columns exist in the ORM but are missing from the "
                    f"database file — queries using them will fail until a migration is "
                    f"run: {sorted(missing_columns)}"
                )
            else:
                logger.info("Column integrity check passed.")

            # ── Step 3: One-off data backfill ────────────────────────────────
            if "albums" in existing_tables and "tracks" in existing_tables:
                self._backfill_album_gain_peak()

        except Exception as e:
            logger.error(f"Integrity check failed: {e}")
            raise

    def _rename_column_if_needed(
        self, table_name: str, old_name: str, new_name: str
    ) -> None:
        """Rename a column in-place via SQLite's RENAME COLUMN, if the old
        name is still present and the new one isn't already there. This is
        for renames only — SQLite's ADD COLUMN path in _try_add_column can't
        express "this is the same data under a new name", so without this
        step a rename would look like an add and leave the old column (and
        its data) behind as dead weight.
        """
        try:
            inspector = inspect(self.engine)
            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if old_name not in columns or new_name in columns:
                return
            ddl = (
                f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" '
                f'TO "{new_name}"'
            )
            with self.engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(
                f"Renamed column {table_name}.{old_name} to {new_name}."
            )
        except Exception as e:
            logger.error(
                f"Failed to rename column {table_name}.{old_name} to "
                f"{new_name}: {e}"
            )

    def _backfill_album_gain_peak(self) -> None:
        """One-time catch-up for albums analyzed before album_gain/album_peak
        became a computed field (see src/statistics/album_gain_peak.py).
        Only touches albums that have never been computed (album_gain IS
        NULL), so it's a no-op on every startup after the first, and it
        never overwrites a value produced by the normal analysis pipeline.
        """
        try:
            with self.Session() as session:
                albums = (
                    session.query(Album).filter(Album.album_gain.is_(None)).all()
                )
                if not albums:
                    return

                updated = 0
                for album in albums:
                    tracks = (
                        session.query(Track)
                        .filter(Track.album_id == album.album_id)
                        .all()
                    )
                    if not tracks:
                        continue
                    album_gain, album_peak = compute_album_gain_peak(tracks)
                    if album_gain is None and album_peak is None:
                        continue
                    album.album_gain = album_gain
                    album.album_peak = album_peak
                    updated += 1

                if updated:
                    session.commit()
                    logger.info(
                        f"Backfilled album_gain/album_peak for {updated} album(s) "
                        f"from existing track analysis data."
                    )
        except Exception as e:
            logger.error(f"Album gain/peak backfill failed: {e}")

    def _try_add_column(self, table_name: str, column) -> bool:
        """Attempt to add a missing column to an existing table via ALTER TABLE.

        Only safe, additive cases are handled: SQLite's ADD COLUMN can't
        attach a PRIMARY KEY or UNIQUE constraint, and a NOT NULL column
        requires a constant DEFAULT. A FOREIGN KEY is fine as long as the
        column is nullable — SQLite implicitly defaults it to NULL for
        every existing row, which trivially satisfies the constraint;
        there's just no safe way to backfill a NOT NULL FK with no
        existing row to point at. Anything outside that (composite keys,
        unique columns, NOT NULL with no default) is left for the caller
        to report instead of guessing at a migration.
        """
        if column.primary_key or column.unique:
            return False

        if column.foreign_keys and not column.nullable:
            return False

        has_scalar_default = column.default is not None and getattr(
            column.default, "is_scalar", False
        )
        if not column.nullable and not has_scalar_default:
            return False

        col_type = column.type.compile(dialect=self.engine.dialect)
        ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
        if not column.nullable:
            ddl += " NOT NULL"
        if has_scalar_default:
            ddl += f" DEFAULT {column.default.arg!r}"

        try:
            with self.engine.begin() as conn:
                conn.execute(text(ddl))
            return True
        except Exception as e:
            logger.error(f"Failed to auto-add column {table_name}.{column.name}: {e}")
            return False
