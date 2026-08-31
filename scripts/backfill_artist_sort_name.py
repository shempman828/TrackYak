"""
One-time backfill for the new Artist.sort_name column.

For every Artist row with an MBID but no sort_name on file, look up the
artist directly on MusicBrainz and fill in its `sort-name` -- the filing
name MB keeps alongside the display name ("Beatles, The", "Davis, Miles",
"Sigur Rós"). `sort-name` is in the base get_artist_by_id response, so no
`includes` are needed.

Only ever fills a currently-NULL sort_name -- never overwrites a value
already on file, and an artist MB has no sort-name for is left NULL rather
than guessed. Safe to re-run (the guard is `not a.sort_name`).

Run once, manually, from the repo root:

    python scripts/backfill_artist_sort_name.py
"""

from datetime import datetime
from pathlib import Path
import shutil

import musicbrainzngs
from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError, configure

DB_PATH = "music_library.db"


class _MinimalController:
    """Boots only the database layer, same pattern as
    scripts/backfill_album_media_format.py's _MinimalController -- no
    GUI/audio deps needed for a data patch. Going through MusicDatabase
    (not a bare create_engine) is also what auto-adds the new sort_name
    column to the existing artists table before the pass runs."""

    def __init__(self):
        self.db = MusicDatabase(f"sqlite:///{DB_PATH}")
        self.engine = self.db.engine
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

    def close_session(self):
        self.SessionFactory.remove()


def _backup(db_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path("backups").mkdir(parents=True, exist_ok=True)
    backup_path = f"backups/{Path(db_path).name}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _fetch_sort_name(mbid: str) -> str | None:
    """Direct MusicBrainz artist lookup for the sort-name only."""
    configure()
    try:
        result = musicbrainzngs.get_artist_by_id(mbid)
    except Exception as e:
        logger.warning(f"Could not fetch artist {mbid}: {e}")
        return None
    return (result.get("artist") or {}).get("sort-name") or None


def backfill_sort_name(controller, *, fetch=_fetch_sort_name):
    """Fill Artist.sort_name for every artist with an MBID and no sort_name
    on file. `fetch` maps an MBID to a sort-name string (or None); it's
    injectable so tests can run without touching the network. Returns
    (filled, unavailable, failed) lists of human-readable strings."""
    artists = controller.get.get_all_entities("Artist") or []
    pending = [a for a in artists if a.MBID and not a.sort_name]

    filled, unavailable, failed = [], [], []

    for artist in pending:
        try:
            sort_name = fetch(artist.MBID)
        except MusicBrainzLookupError as e:
            logger.warning(
                f"Could not resolve sort name for {artist.artist_name!r} ({artist.MBID}): {e}"
            )
            failed.append(artist.artist_name)
            continue

        if not sort_name:
            unavailable.append(artist.artist_name)
            continue

        ok = controller.update.update_entity("Artist", artist.artist_id, sort_name=sort_name)
        if ok:
            filled.append(f"{artist.artist_name} -> {sort_name}")
        else:
            failed.append(artist.artist_name)

    return filled, unavailable, failed


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        pending_count = sum(
            1
            for a in (controller.get.get_all_entities("Artist") or [])
            if a.MBID and not a.sort_name
        )
        print(f"{pending_count} artist(s) have an MBID and no sort_name on file.")

        filled, unavailable, failed = backfill_sort_name(controller)

        print(f"\nsort_name backfilled for {len(filled)} artist(s):")
        for line in filled:
            print(f"  {line}")

        if unavailable:
            print(f"\nNo sort name available from MusicBrainz for {len(unavailable)} artist(s):")
            for name in unavailable:
                print(f"  {name}")

        if failed:
            print(f"\nFailed to resolve/write sort name for {len(failed)} artist(s):")
            for name in failed:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
