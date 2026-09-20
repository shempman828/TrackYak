"""
One-time migration for docs/specs/artist_tags.md: move the Religion model's
data into the new generic Tag model, then remove Religion from the schema.

What it does, all in a single transaction:
  1. Makes a timestamped backup copy of the DB (backups/, last 3 kept).
  2. Creates the tag_types / tags / artist_tag_associations tables if they
     don't already exist (in case this runs before the app has ever started
     up with the new ORM models).
  3. Creates (or reuses) a "Religion" TagType.
  4. Walks the religions table parent-first, creating a matching Tag row
     per Religion row (same name/description; parent_id remapped from
     religion_id to the new tag_id).
  5. Inserts one artist_tag_associations row per artists.religion_id.
  6. Rebuilds the artists table without the religion_id column (SQLite
     can't ALTER TABLE ... DROP COLUMN a column while any table's foreign
     keys are being enforced against the table being changed, so this
     rebuilds "artists" from the current ORM definition instead -- same
     technique, and the same foreign_keys=OFF/rebuild/foreign_key_check
     bracket, as MusicDatabase._drop_artist_name_unique_constraint_if_needed).
  7. Drops the religions table.

Dry run by default (reports, touches nothing). Pass --apply to write; a
timestamped copy of the DB is made under backups/ first (last 3 kept).

Run from the repo root:

    python scripts/migrate_religion_to_tags.py            # dry run
    python scripts/migrate_religion_to_tags.py --apply
"""

import argparse
from pathlib import Path
import sqlite3
import sys

from sqlalchemy import MetaData, create_engine
from sqlalchemy.schema import CreateIndex, CreateTable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._db_backup import backup_db
from src.db.db_tables import Artist, Base

DB_PATH = "music_library.db"
RELIGION_TAG_TYPE_NAME = "Religion"


def _table_exists(cursor, name: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def _create_new_tables_if_missing(engine) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables["tag_types"],
            Base.metadata.tables["tags"],
            Base.metadata.tables["artist_tag_associations"],
        ],
    )


def _migrate_religions_to_tags(cursor) -> tuple[int, int]:
    """Create the "Religion" TagType, a Tag per Religion row (parent-first,
    so a child's parent_id always has a mapped tag to point at), and one
    artist_tag_associations row per artists.religion_id. Returns (tags
    created, assignments migrated)."""
    cursor.execute(
        "SELECT tag_type_id FROM tag_types WHERE type_name = ?", (RELIGION_TAG_TYPE_NAME,)
    )
    row = cursor.fetchone()
    if row:
        tag_type_id = row[0]
    else:
        # sort_order is NOT NULL with no DB-level default (see
        # src/db/db_tables/tag.py) -- append after whatever categories the
        # user may have already created by hand before running this
        # migration, same as TagTypeManagerDialog._add.
        cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tag_types")
        next_sort_order = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO tag_types (type_name, sort_order) VALUES (?, ?)",
            (RELIGION_TAG_TYPE_NAME, next_sort_order),
        )
        tag_type_id = cursor.lastrowid

    cursor.execute("SELECT religion_id, religion_name, parent_id, description FROM religions")
    religions = cursor.fetchall()

    tag_id_by_religion_id: dict[int, int] = {}
    remaining = list(religions)
    while remaining:
        still_remaining = []
        progressed = False
        for religion_id, religion_name, parent_id, description in remaining:
            if parent_id is not None and parent_id not in tag_id_by_religion_id:
                still_remaining.append((religion_id, religion_name, parent_id, description))
                continue
            parent_tag_id = tag_id_by_religion_id.get(parent_id) if parent_id else None
            cursor.execute(
                "INSERT INTO tags (tag_name, description, parent_id, tag_type_id) "
                "VALUES (?, ?, ?, ?)",
                (religion_name, description, parent_tag_id, tag_type_id),
            )
            tag_id_by_religion_id[religion_id] = cursor.lastrowid
            progressed = True
        if not progressed:
            unresolved = ", ".join(str(r[0]) for r in still_remaining)
            raise RuntimeError(
                f"Could not resolve parent chain for religion_id(s) {unresolved} -- a "
                "parent_id points at a religion_id that doesn't exist. Aborting; this "
                "whole transaction rolls back, so nothing was changed."
            )
        remaining = still_remaining

    cursor.execute("SELECT artist_id, religion_id FROM artists WHERE religion_id IS NOT NULL")
    assignments_migrated = 0
    for artist_id, religion_id in cursor.fetchall():
        tag_id = tag_id_by_religion_id.get(religion_id)
        if tag_id is None:
            continue
        cursor.execute(
            "INSERT OR IGNORE INTO artist_tag_associations (artist_id, tag_id) VALUES (?, ?)",
            (artist_id, tag_id),
        )
        assignments_migrated += 1

    return len(religions), assignments_migrated


def _rebuild_artists_table_without_religion_id(cursor, engine) -> None:
    """Drop the religion_id column from artists via a table rebuild: create
    a fresh 'artists_new' from the current (religion-free) ORM definition,
    copy every other column across, drop the old table, rename the new one
    into place, then recreate its indexes."""
    cursor.execute("PRAGMA table_info(artists)")
    old_columns = {row[1] for row in cursor.fetchall()}
    shared_columns = [c.name for c in Artist.__table__.columns if c.name in old_columns]
    col_list = ", ".join(f'"{c}"' for c in shared_columns)

    scratch_meta = MetaData()
    artists_new = Artist.__table__.to_metadata(scratch_meta, name="artists_new")
    # Indexes are recreated by name below, against the real "artists" name,
    # after the rebuild -- creating them now against "artists_new" would
    # collide with the live old table's identically-named indexes, since
    # both tables exist side by side until the DROP.
    artists_new.indexes.clear()

    cursor.execute(str(CreateTable(artists_new).compile(engine)).strip())
    cursor.execute(f'INSERT INTO "artists_new" ({col_list}) SELECT {col_list} FROM "artists"')
    cursor.execute('DROP TABLE "artists"')
    cursor.execute('ALTER TABLE "artists_new" RENAME TO "artists"')

    cursor.execute("PRAGMA index_list(artists)")
    existing_indexes = {row[1] for row in cursor.fetchall()}
    for index in Artist.__table__.indexes:
        if index.name not in existing_indexes:
            cursor.execute(str(CreateIndex(index).compile(engine)).strip())


def _apply(db_path: str) -> tuple[int, int]:
    """Run the whole migration as one all-or-nothing transaction, matching
    MusicDatabase._drop_artist_name_unique_constraint_if_needed's use of a
    raw connection: PRAGMA foreign_keys must be toggled outside any active
    transaction to take effect, which rules out SQLAlchemy's engine.begin()
    (it starts a transaction before script code gets to run anything)."""
    engine = create_engine(f"sqlite:///{db_path}")
    raw_conn = sqlite3.connect(db_path)
    try:
        cursor = raw_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        try:
            cursor.execute("BEGIN")
            religion_count, assignments_migrated = _migrate_religions_to_tags(cursor)
            _rebuild_artists_table_without_religion_id(cursor, engine)
            cursor.execute('DROP TABLE "religions"')

            cursor.execute("PRAGMA foreign_key_check")
            violations = cursor.fetchall()
            if violations:
                raise RuntimeError(f"Foreign key check failed after migration: {violations}")

            raw_conn.commit()
        except Exception:
            raw_conn.rollback()
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        raw_conn.close()
        engine.dispose()

    return religion_count, assignments_migrated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument(
        "--db", default=DB_PATH, help=f"path to the library DB (default: {DB_PATH})"
    )
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"DB not found: {args.db}")
        return 1

    inspect_conn = sqlite3.connect(args.db)
    try:
        cursor = inspect_conn.cursor()
        if not _table_exists(cursor, "religions"):
            print(
                "No 'religions' table found -- already migrated, or this DB never had "
                "Religion data. Nothing to do."
            )
            return 0

        cursor.execute("SELECT COUNT(*) FROM religions")
        religions = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM artists WHERE religion_id IS NOT NULL")
        assignments = cursor.fetchone()[0]
    finally:
        inspect_conn.close()

    print(f"{religions} religion(s), {assignments} artist assignment(s) to migrate.")

    if not args.apply:
        print("Dry run -- no changes written. Re-run with --apply to migrate this data.")
        return 0

    backup = backup_db(args.db, tag="pre-religion-to-tags")
    print(f"DB backed up to {backup}")

    setup_engine = create_engine(f"sqlite:///{args.db}")
    _create_new_tables_if_missing(setup_engine)
    setup_engine.dispose()

    religion_count, assignments_migrated = _apply(args.db)

    print(
        f"Migrated {religion_count} religion(s) into TagType '{RELIGION_TAG_TYPE_NAME}' and "
        f"{assignments_migrated} artist assignment(s). Dropped the religions table and the "
        f"artists.religion_id column."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
