"""
MusicDatabase: engine/session setup plus schema and integrity verification.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.album.release_type_utils import normalize_release_type
from src.core.logger_config import logger
from src.db.db_engine import engine as _shared_engine
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
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
                _shared_engine
                if db_path == _DEFAULT_DB_PATH
                else create_engine(db_path, echo=False)
            )
            self.Session = sessionmaker(bind=self.engine)
            self._initialize_database()
            self._verify_integrity()
        except SQLAlchemyError as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _initialize_database(self):
        """Creates the database schema if it doesn't already exist."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables initialized successfully.")
        except SQLAlchemyError as e:
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
                "artist_split_alias",
                "publisher_alias",
                "publisher_split_alias",
                "genre_alias",
                "genre_split_alias",
                "role_alias",
                "role_split_alias",
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
            for table_name in ("albums", "artists", "tracks", "publishers"):
                if table_name in existing_tables:
                    self._rename_column_if_needed(table_name, "is_fixed", "first_pass")

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

            # ── Step 3: Index check ──────────────────────────────────────────
            # Drop indexes retired/superseded in indexes.py that may still
            # linger on an existing database file, then create whatever's
            # missing: create_all() only emits DDL (including indexes) for
            # tables that don't already exist, so an Index() added to a table
            # that was created under an earlier version of the schema is
            # silently never applied to an existing database file.
            self._drop_legacy_indexes()
            self._create_missing_indexes(inspector, existing_tables)

            # ── Step 4: One-off data backfill ────────────────────────────────
            if "albums" in existing_tables and "tracks" in existing_tables:
                self._backfill_album_gain_peak()
            if "albums" in existing_tables:
                self._normalize_release_type_casing()

            # ── Step 5: FTS5 virtual table setup ─────────────────────────────
            if "chart_entries" in existing_tables:
                self._ensure_chart_entries_fts()

        except SQLAlchemyError as e:
            logger.error(f"Integrity check failed: {e}")
            raise

    # Indexes retired from indexes.py because they duplicated a PK/UNIQUE
    # constraint, duplicated another explicit index, or matched no real query
    # pattern in the app. Named explicitly (rather than dropping "anything not
    # in metadata") so this can't accidentally drop an index this code doesn't
    # know about.
    _RETIRED_INDEXES = {
        "idx_artists_name",  # duplicate of the artist_name unique constraint
        "idx_tracks_path",  # duplicate of the track_file_path unique constraint
        "idx_tracks_disc_id",  # exact duplicate of idx_track_disc_id
        "idx_artist_begin_end",  # no query filters/sorts on begin_year/end_year
        "idx_track_genres",  # duplicate of the track_genres composite primary key
        "idx_artist_type_assoc",  # duplicate of the artist_type_associations composite primary key
        "idx_mood_track_association",  # duplicate of the mood_track_association composite primary key
        "ix_album_publisher_unique",  # duplicate of the album_publisher composite primary key
        "idx_track_usages_type",  # usage_type is never filtered on its own
        "idx_album_roles",  # redundant leftmost prefix of uq_album_artist_role
        "idx_place_assoc_entity_type",  # superseded by idx_place_assoc_entity_type_id
        "idx_award_assoc_entity_type",  # superseded by idx_award_assoc_entity_type_id
    }

    def _drop_legacy_indexes(self) -> None:
        """Drop indexes named in _RETIRED_INDEXES if they still exist."""
        try:
            inspector = inspect(self.engine)
            for table_name in inspector.get_table_names():
                present = {idx["name"] for idx in inspector.get_indexes(table_name)}
                for index_name in present & self._RETIRED_INDEXES:
                    with self.engine.begin() as conn:
                        conn.execute(text(f'DROP INDEX "{index_name}"'))
                    logger.info(f"Dropped retired index {index_name} on {table_name}.")
        except SQLAlchemyError as e:
            logger.error(f"Failed to drop retired indexes: {e}")

    def _create_missing_indexes(self, inspector, existing_tables) -> None:
        """Create any Index(...) defined on the ORM models that isn't yet
        present on the corresponding (already-existing) database table.
        """
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue  # create_all() already created it, indexes included

            existing_indexes = {
                idx["name"] for idx in inspector.get_indexes(table_name)
            }
            for index in table.indexes:
                if index.name in existing_indexes:
                    continue
                try:
                    index.create(bind=self.engine)
                    logger.info(f"Created missing index {index.name} on {table_name}.")
                except SQLAlchemyError as e:
                    logger.error(f"Failed to create index {index.name}: {e}")

    def _ensure_chart_entries_fts(self) -> None:
        """Create the chart_entries_fts FTS5 virtual table if it doesn't
        already exist, backed by chart_entries as an external-content table
        (no duplicated storage) and kept in sync via triggers, so a row
        change made anywhere -- ORM, bulk import, or raw SQL -- keeps the
        index current without every write path needing to know it exists.

        This is the first virtual table in the schema, needed because
        title/artist search over chart_entries (~975K rows at full scale)
        uses a leading wildcard LIKE that can't use a B-tree index; FTS5
        gives that search an actual index instead of a table scan. See
        src/charts/chart_search_tab.py.
        """
        try:
            with self.engine.begin() as conn:
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'chart_entries_fts'"
                    )
                ).first()
                if exists:
                    return

                conn.execute(
                    text(
                        "CREATE VIRTUAL TABLE chart_entries_fts USING fts5("
                        "raw_title, raw_performer, "
                        "content='chart_entries', content_rowid='chart_entry_id'"
                        ")"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO chart_entries_fts(rowid, raw_title, raw_performer) "
                        "SELECT chart_entry_id, raw_title, raw_performer FROM chart_entries"
                    )
                )

                # Standard external-content FTS5 sync triggers (per SQLite docs):
                # a plain row op mirrors into the index; an update is a delete
                # of the old row image followed by an insert of the new one.
                conn.execute(
                    text(
                        """
                        CREATE TRIGGER chart_entries_fts_ai
                        AFTER INSERT ON chart_entries
                        BEGIN
                            INSERT INTO chart_entries_fts(rowid, raw_title, raw_performer)
                            VALUES (new.chart_entry_id, new.raw_title, new.raw_performer);
                        END
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        CREATE TRIGGER chart_entries_fts_ad
                        AFTER DELETE ON chart_entries
                        BEGIN
                            INSERT INTO chart_entries_fts(chart_entries_fts, rowid, raw_title, raw_performer)
                            VALUES ('delete', old.chart_entry_id, old.raw_title, old.raw_performer);
                        END
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        CREATE TRIGGER chart_entries_fts_au
                        AFTER UPDATE ON chart_entries
                        BEGIN
                            INSERT INTO chart_entries_fts(chart_entries_fts, rowid, raw_title, raw_performer)
                            VALUES ('delete', old.chart_entry_id, old.raw_title, old.raw_performer);
                            INSERT INTO chart_entries_fts(rowid, raw_title, raw_performer)
                            VALUES (new.chart_entry_id, new.raw_title, new.raw_performer);
                        END
                        """
                    )
                )
            logger.info(
                "Created chart_entries_fts FTS5 virtual table and sync triggers."
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to create chart_entries_fts: {e}")

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
                f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"'
            )
            with self.engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(f"Renamed column {table_name}.{old_name} to {new_name}.")
        except SQLAlchemyError as e:
            logger.error(
                f"Failed to rename column {table_name}.{old_name} to {new_name}: {e}"
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
                albums = session.query(Album).filter(Album.album_gain.is_(None)).all()
                if not albums:
                    return

                updated = 0
                for album in albums:
                    tracks = (
                        session
                        .query(Track)
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
        except SQLAlchemyError as e:
            logger.error(f"Album gain/peak backfill failed: {e}")

    def _normalize_release_type_casing(self) -> None:
        """One-time catch-up for albums saved before release_type was
        normalized to a canonical casing (see src/album/release_type_utils.py),
        so pre-existing rows like "album" and "Album" collapse onto the same
        value instead of being treated as distinct types. Only touches rows
        that actually differ from their normalized form, so it's a no-op on
        every startup after the first.
        """
        try:
            with self.Session() as session:
                albums = (
                    session.query(Album).filter(Album.release_type.isnot(None)).all()
                )
                updated = 0
                for album in albums:
                    normalized = normalize_release_type(album.release_type)
                    if normalized != album.release_type:
                        album.release_type = normalized
                        updated += 1

                if updated:
                    session.commit()
                    logger.info(
                        f"Normalized release_type casing for {updated} album(s)."
                    )
        except SQLAlchemyError as e:
            logger.error(f"release_type casing normalization failed: {e}")

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
        except SQLAlchemyError as e:
            logger.error(f"Failed to auto-add column {table_name}.{column.name}: {e}")
            return False
