"""
One-time backfill for the new Album.media_format column.

For every Album row with an MBID but no media_format on file, look up the
release directly on MusicBrainz and fill in the carrier -- the distinct
per-medium `format` strings, sorted and "/"-joined (e.g. "CD", '12" Vinyl',
"CD/DVD-Video"). `format` lives on each medium, so this needs
`includes=["media"]` on get_release_by_id -- still a lightweight per-album
lookup, not a full fetch_release_detail() call.

Only ever fills a currently-NULL media_format -- never overwrites a value
already on file, and a release whose media carry no format on MusicBrainz
(genuinely unset there) is left NULL rather than guessed. Safe to re-run.

Run once, manually, from the repo root:

    python scripts/backfill_album_media_format.py
"""

from datetime import datetime
from pathlib import Path
import shutil

import musicbrainzngs
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError, configure
from src.musicbrainz.musicbrainz_release import _media_format_str

DB_PATH = "music_library.db"


class _MinimalController:
    """Boots only the database layer, same pattern as
    scripts/backfill_album_release_country.py's _MinimalController -- no
    GUI/audio deps needed for a data patch."""

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


def _fetch_media_format(mbid: str) -> str | None:
    """Direct MusicBrainz release lookup for the per-medium format only."""
    configure()
    try:
        result = musicbrainzngs.get_release_by_id(mbid, includes=["media"])
    except Exception as e:
        logger.warning(f"Could not fetch release {mbid}: {e}")
        return None
    return _media_format_str((result.get("release") or {}).get("medium-list"))


def backfill_media_format(controller, *, fetch=_fetch_media_format):
    """Fill Album.media_format for every album with an MBID and no format on
    file. `fetch` maps an MBID to a "/"-joined format string (or None); it's
    injectable so tests can run without touching the network. Returns
    (filled, unavailable, failed) lists of human-readable strings."""
    albums = controller.get.get_all_entities("Album") or []
    pending = [a for a in albums if a.MBID and not a.media_format]

    filled, unavailable, failed = [], [], []

    for album in pending:
        try:
            media_format = fetch(album.MBID)
        except MusicBrainzLookupError as e:
            logger.warning(
                f"Could not resolve media format for {album.album_name!r} ({album.MBID}): {e}"
            )
            failed.append(album.album_name)
            continue

        if not media_format:
            unavailable.append(album.album_name)
            continue

        ok = controller.update.update_entity("Album", album.album_id, media_format=media_format)
        if ok:
            filled.append(f"{album.album_name} -> {media_format}")
        else:
            failed.append(album.album_name)

    return filled, unavailable, failed


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        pending_count = sum(
            1
            for a in (controller.get.get_all_entities("Album") or [])
            if a.MBID and not a.media_format
        )
        print(f"{pending_count} album(s) have an MBID and no media_format on file.")

        filled, unavailable, failed = backfill_media_format(controller)

        print(f"\nmedia_format backfilled for {len(filled)} album(s):")
        for line in filled:
            print(f"  {line}")

        if unavailable:
            print(f"\nNo media format available from MusicBrainz for {len(unavailable)} album(s):")
            for name in unavailable:
                print(f"  {name}")

        if failed:
            print(f"\nFailed to resolve/write media format for {len(failed)} album(s):")
            for name in failed:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
