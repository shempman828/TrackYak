"""
One-off migration for bugs.md #90: replace the single freetext
Artist.artist_type column with the new multi-valued ArtistType model.

Run once, manually, from the repo root:

    python scripts/migrate_artist_types.py

What it does:
  1. Makes a timestamped backup copy of music_library.db.
  2. Creates the artist_types / artist_type_associations tables if they
     don't already exist (in case this runs before the app has ever
     started up with the new ORM models).
  3. For every distinct non-null legacy artist_type value EXCEPT "Person"
     and "Band" (dropped -- leftovers of the old entity-kind concept, not
     real canonical roles), finds-or-creates a matching ArtistType row and
     links every artist that had that value.
  4. Drops the now-unused artist_type column from artists.
"""

import os
import shutil
import sqlite3
from datetime import datetime

DB_PATH = "music_library.db"
SKIPPED_VALUES = {"Person", "Band"}

CREATE_ARTIST_TYPES = """
CREATE TABLE IF NOT EXISTS artist_types (
    artist_type_id INTEGER PRIMARY KEY,
    type_name VARCHAR NOT NULL UNIQUE,
    type_description VARCHAR
)
"""

CREATE_ARTIST_TYPE_ASSOCIATIONS = """
CREATE TABLE IF NOT EXISTS artist_type_associations (
    artist_id INTEGER NOT NULL REFERENCES artists (artist_id) ON DELETE CASCADE,
    artist_type_id INTEGER NOT NULL REFERENCES artist_types (artist_type_id) ON DELETE CASCADE,
    PRIMARY KEY (artist_id, artist_type_id)
)
"""


def _backup(db_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("backups", exist_ok=True)
    backup_path = f"backups/{os.path.basename(db_path)}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    if not _has_column(conn, "artists", "artist_type"):
        print("artists.artist_type already gone -- nothing to migrate.")
        conn.close()
        return

    conn.execute(CREATE_ARTIST_TYPES)
    conn.execute(CREATE_ARTIST_TYPE_ASSOCIATIONS)

    rows = conn.execute(
        "SELECT artist_id, artist_type FROM artists "
        "WHERE artist_type IS NOT NULL AND TRIM(artist_type) != ''"
    ).fetchall()

    by_value: dict[str, list[int]] = {}
    skipped_count = 0
    for artist_id, value in rows:
        value = value.strip()
        if value in SKIPPED_VALUES:
            skipped_count += 1
            continue
        by_value.setdefault(value, []).append(artist_id)

    types_created = 0
    types_reused = 0
    associations_created = 0

    for type_name, artist_ids in by_value.items():
        existing = conn.execute(
            "SELECT artist_type_id FROM artist_types WHERE type_name = ?",
            (type_name,),
        ).fetchone()
        if existing:
            artist_type_id = existing[0]
            types_reused += 1
        else:
            cur = conn.execute(
                "INSERT INTO artist_types (type_name) VALUES (?)", (type_name,)
            )
            artist_type_id = cur.lastrowid
            types_created += 1

        for artist_id in artist_ids:
            cur = conn.execute(
                "INSERT OR IGNORE INTO artist_type_associations "
                "(artist_id, artist_type_id) VALUES (?, ?)",
                (artist_id, artist_type_id),
            )
            associations_created += cur.rowcount

    conn.execute("ALTER TABLE artists DROP COLUMN artist_type")
    conn.commit()
    conn.close()

    print(
        f"Migrated {associations_created} artist-type assignment(s) across "
        f"{types_created} new + {types_reused} reused ArtistType row(s)."
    )
    print(f"Dropped {skipped_count} legacy Person/Band value(s) without migrating.")
    print("Dropped artists.artist_type column.")


if __name__ == "__main__":
    main()
