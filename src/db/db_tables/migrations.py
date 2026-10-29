"""One-off schema migrations and data backfills run by MusicDatabase at startup."""

from collections import defaultdict
import re

from sqlalchemy import inspect, literal, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.album.release_type_utils import normalize_release_type
from src.analysis.album_gain_peak import compute_album_gain_peak
from src.db.db_tables.album import Album, AlbumVirtualTrack
from src.db.db_tables.base import Base
from src.db.db_tables.place import PlaceAssociation
from src.db.db_tables.track import Track
from src.foundation.logger_config import logger

# Named explicitly (not "anything absent from metadata") so an unknown index is never dropped.
RETIRED_INDEXES = frozenset(
    {
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
)


def rename_column_if_needed(engine, table_name: str, old_name: str, new_name: str) -> None:
    """Rename old_name to new_name in place, keeping its data, if the rename is still pending."""
    try:
        columns = {col["name"] for col in inspect(engine).get_columns(table_name)}
        if old_name not in columns or new_name in columns:
            return
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"'))
        logger.info(f"Renamed column {table_name}.{old_name} to {new_name}.")
    except SQLAlchemyError as e:
        logger.error(f"Failed to rename column {table_name}.{old_name} to {new_name}: {e}")


def drop_artist_name_unique_constraint_if_needed(engine) -> None:
    """Rebuild the artists table without its legacy UNIQUE(artist_name) constraint."""
    # SQLite cannot ALTER a constraint away, so this follows SQLite's documented
    # table-rebuild procedure and keeps the live column set verbatim.
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='artists'")
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return
        create_sql = row[0]
        if not re.search(r"UNIQUE\s*\(\s*artist_name\s*\)", create_sql):
            return

        cursor.execute("PRAGMA table_info(artists)")
        columns = [info[1] for info in cursor.fetchall()]
        col_list = ", ".join(f'"{c}"' for c in columns)

        new_sql = re.sub(r"UNIQUE\s*\(\s*artist_name\s*\)\s*,\s*", "", create_sql, count=1)
        new_sql = new_sql.replace("CREATE TABLE artists (", "CREATE TABLE artists_new (", 1)
        if "artists_new" not in new_sql:
            raise SQLAlchemyError("Could not safely rewrite the artists table's CREATE statement for the artist_name migration -- aborting rather than risk a malformed rebuild.")

        cursor.execute("PRAGMA foreign_keys=OFF")
        try:
            cursor.execute("BEGIN")
            cursor.execute(new_sql)
            cursor.execute(f"INSERT INTO artists_new ({col_list}) SELECT {col_list} FROM artists")
            cursor.execute("DROP TABLE artists")
            cursor.execute("ALTER TABLE artists_new RENAME TO artists")
            cursor.execute("PRAGMA foreign_key_check")
            violations = cursor.fetchall()
            if violations:
                raise SQLAlchemyError(f"Foreign key check failed after artist_name migration, rolling back: {violations}")
            raw_conn.commit()
            logger.info("Migrated artists table: dropped the UNIQUE(artist_name) constraint so MB import can create a second same-named Artist instead of merging two different real people.")
        except Exception:
            raw_conn.rollback()
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
    except SQLAlchemyError as e:
        logger.error(f"Failed to drop artist_name unique constraint: {e}")
        raise
    finally:
        raw_conn.close()


def try_add_column(engine, table_name: str, column) -> bool:
    """Add a missing column via ALTER TABLE if SQLite can do it safely; return True on success."""
    # SQLite ADD COLUMN cannot add PRIMARY KEY/UNIQUE, needs a constant DEFAULT
    # for NOT NULL, and cannot backfill a NOT NULL foreign key.
    if column.primary_key or column.unique:
        return False

    if column.foreign_keys and not column.nullable:
        return False

    has_scalar_default = column.default is not None and getattr(column.default, "is_scalar", False)
    if not column.nullable and not has_scalar_default:
        return False

    col_type = column.type.compile(dialect=engine.dialect)
    ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
    if not column.nullable:
        ddl += " NOT NULL"
    if has_scalar_default:
        default_sql = literal(column.default.arg).compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
        ddl += f" DEFAULT {default_sql}"

    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        return True
    except SQLAlchemyError as e:
        logger.error(f"Failed to auto-add column {table_name}.{column.name}: {e}")
        return False


def drop_retired_indexes(engine) -> None:
    """Drop indexes named in RETIRED_INDEXES if they still exist."""
    try:
        inspector = inspect(engine)
        for table_name in inspector.get_table_names():
            present = {idx["name"] for idx in inspector.get_indexes(table_name)}
            for index_name in present & RETIRED_INDEXES:
                with engine.begin() as conn:
                    conn.execute(text(f'DROP INDEX "{index_name}"'))
                logger.info(f"Dropped retired index {index_name} on {table_name}.")
    except SQLAlchemyError as e:
        logger.error(f"Failed to drop retired indexes: {e}")


def create_missing_indexes(engine, existing_tables) -> None:
    """Create each ORM Index that is not yet on its already-existing table."""
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # create_all() already created it, indexes included

        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        for index in table.indexes:
            if index.name in existing_indexes:
                continue
            try:
                index.create(bind=engine)
                logger.info(f"Created missing index {index.name} on {table_name}.")
            except SQLAlchemyError as e:
                logger.error(f"Failed to create index {index.name}: {e}")


def _index_exists(engine, table_name: str, index_name: str) -> bool:
    """True if index_name is already present on table_name."""
    return any(idx["name"] == index_name for idx in inspect(engine).get_indexes(table_name))


def dedupe_place_associations(engine) -> None:
    """Delete duplicate (entity, place, type) place_associations rows so their unique index can be built."""
    try:
        if _index_exists(engine, "place_associations", "uq_place_assoc_entity_place_type"):
            return  # the index already blocks duplicates
        with Session(engine) as session:
            rows = (
                session.query(PlaceAssociation.association_id, PlaceAssociation.entity_type, PlaceAssociation.entity_id, PlaceAssociation.place_id, PlaceAssociation.association_type_id)
                .order_by(PlaceAssociation.association_id)
                .all()
            )

            seen_keys = set()
            duplicate_ids = []
            for row in rows:
                # SQLite unique indexes treat NULLs as distinct, so untyped rows never collide.
                if row.association_type_id is None:
                    continue
                key = (row.entity_type, row.entity_id, row.place_id, row.association_type_id)
                if key in seen_keys:
                    duplicate_ids.append(row.association_id)  # keeps the oldest row of each group
                else:
                    seen_keys.add(key)

            if duplicate_ids:
                session.query(PlaceAssociation).filter(PlaceAssociation.association_id.in_(duplicate_ids)).delete(synchronize_session=False)
                session.commit()
                logger.info(f"Removed {len(duplicate_ids)} duplicate place_associations row(s) (same entity, place, and association type).")
    except SQLAlchemyError as e:
        logger.error(f"Duplicate place_associations cleanup failed: {e}")


def dedupe_album_virtual_tracks(engine) -> None:
    """Delete orphaned and duplicate album_virtual_tracks rows so their unique index can be built."""
    try:
        if _index_exists(engine, "album_virtual_tracks", "uq_album_virtual_tracks_album_track"):
            return  # the index already blocks duplicates
        with Session(engine) as session:
            # Orphans come from track deletes made before the ORM cascaded these rows.
            orphaned = session.query(AlbumVirtualTrack).filter((AlbumVirtualTrack.album_id.is_(None)) | (AlbumVirtualTrack.track_id.is_(None))).delete(synchronize_session=False)

            seen_keys = set()
            duplicate_ids = []
            for row in session.query(AlbumVirtualTrack.virtual_id, AlbumVirtualTrack.album_id, AlbumVirtualTrack.track_id).order_by(AlbumVirtualTrack.virtual_id):
                key = (row.album_id, row.track_id)
                if key in seen_keys:
                    duplicate_ids.append(row.virtual_id)  # keeps the oldest row of each pair
                else:
                    seen_keys.add(key)
            if duplicate_ids:
                session.query(AlbumVirtualTrack).filter(AlbumVirtualTrack.virtual_id.in_(duplicate_ids)).delete(synchronize_session=False)

            if orphaned or duplicate_ids:
                session.commit()
                logger.info(f"Removed {orphaned} orphaned and {len(duplicate_ids)} duplicate album_virtual_tracks row(s).")
    except SQLAlchemyError as e:
        logger.error(f"album_virtual_tracks cleanup failed: {e}")


def backfill_album_gain_peak(engine) -> None:
    """Compute album_gain/album_peak for albums that have none yet, from their tracks' analysis."""
    try:
        with Session(engine) as session:
            # One query for all candidate tracks instead of one per album.
            tracks_by_album = defaultdict(list)
            for track in session.query(Track).join(Album, Track.album_id == Album.album_id).filter(Album.album_gain.is_(None)):
                tracks_by_album[track.album_id].append(track)
            if not tracks_by_album:
                return

            albums = {a.album_id: a for a in session.query(Album).filter(Album.album_id.in_(list(tracks_by_album)))}
            updated = 0
            for album_id, tracks in tracks_by_album.items():
                album_gain, album_peak = compute_album_gain_peak(tracks)
                if album_gain is None and album_peak is None:
                    continue
                albums[album_id].album_gain = album_gain
                albums[album_id].album_peak = album_peak
                updated += 1

            if updated:
                session.commit()
                logger.info(f"Backfilled album_gain/album_peak for {updated} album(s) from existing track analysis data.")
    except SQLAlchemyError as e:
        logger.error(f"Album gain/peak backfill failed: {e}")


def normalize_release_type_casing(engine) -> None:
    """Rewrite album release_type values to their canonical casing (see release_type_utils)."""
    try:
        with Session(engine) as session:
            updated = 0
            for album in session.query(Album).filter(Album.release_type.isnot(None)):
                normalized = normalize_release_type(album.release_type)
                if normalized != album.release_type:
                    album.release_type = normalized
                    updated += 1

            if updated:
                session.commit()
                logger.info(f"Normalized release_type casing for {updated} album(s).")
    except SQLAlchemyError as e:
        logger.error(f"release_type casing normalization failed: {e}")


def ensure_chart_entries_fts(engine) -> None:
    """Create the chart_entries_fts FTS5 index and its sync triggers if missing."""
    # Leading-wildcard title/artist search cannot use a B-tree index; triggers
    # keep the external-content index current for every write path.
    try:
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chart_entries_fts'")).first()
            if exists:
                return

            conn.execute(text("CREATE VIRTUAL TABLE chart_entries_fts USING fts5(raw_title, raw_performer, content='chart_entries', content_rowid='chart_entry_id')"))
            conn.execute(text("INSERT INTO chart_entries_fts(rowid, raw_title, raw_performer) SELECT chart_entry_id, raw_title, raw_performer FROM chart_entries"))

            # Standard external-content FTS5 sync triggers (per SQLite docs):
            # an update is a delete of the old row image plus an insert of the new one.
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
                        INSERT INTO chart_entries_fts(chart_entries_fts, rowid, raw_title,
                            raw_performer)
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
                        INSERT INTO chart_entries_fts(chart_entries_fts, rowid, raw_title,
                            raw_performer)
                        VALUES ('delete', old.chart_entry_id, old.raw_title, old.raw_performer);
                        INSERT INTO chart_entries_fts(rowid, raw_title, raw_performer)
                        VALUES (new.chart_entry_id, new.raw_title, new.raw_performer);
                    END
                    """
                )
            )
        logger.info("Created chart_entries_fts FTS5 virtual table and sync triggers.")
    except SQLAlchemyError as e:
        logger.error(f"Failed to create chart_entries_fts: {e}")
