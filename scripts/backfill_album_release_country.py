"""
One-time backfill for the new Album.release_country column (#367).

For every Album row with an MBID but no release_country on file, look up
the release directly on MusicBrainz and fill it in. `country` is a base
release attribute (parsed by musicbrainzngs.get_release_by_id with no
`includes` needed -- see mbxml.py's release element parsing), so this is a
lightweight per-album lookup, not a full fetch_release_detail() call.

Only ever fills a currently-NULL release_country -- never overwrites a
value already on file, and a release with no country on MusicBrainz
(genuinely unset there, e.g. "XW" worldwide releases sometimes carry no
country at all) is left NULL rather than guessed.

Run once, manually, from the repo root:

    python scripts/backfill_album_release_country.py
"""

from datetime import datetime
import os
import shutil

import musicbrainzngs
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError, configure

DB_PATH = "music_library.db"


class _MinimalController:
    """Boots only the database layer, same pattern as
    scripts/patch_place_mbid_backfill.py's _MinimalController -- no GUI/audio
    deps needed for a data patch."""

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
    os.makedirs("backups", exist_ok=True)
    backup_path = f"backups/{os.path.basename(db_path)}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _fetch_release_country(mbid: str) -> str | None:
    """Direct MusicBrainz release lookup for the country code only."""
    configure()
    try:
        result = musicbrainzngs.get_release_by_id(mbid)
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(f"Could not fetch release {mbid}: {e}")
        return None
    return (result.get("release") or {}).get("country") or None


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        albums = controller.get.get_all_entities("Album") or []
        pending = [a for a in albums if a.MBID and not a.release_country]
        print(f"{len(pending)} album(s) have an MBID and no release_country on file.")

        filled, unavailable, failed = [], [], []

        for album in pending:
            try:
                country = _fetch_release_country(album.MBID)
            except MusicBrainzLookupError as e:
                logger.warning(
                    f"Could not resolve release country for {album.album_name!r} "
                    f"({album.MBID}): {e}"
                )
                failed.append(album.album_name)
                continue

            if not country:
                unavailable.append(album.album_name)
                continue

            ok = controller.update.update_entity(
                "Album", album.album_id, release_country=country
            )
            if ok:
                filled.append(f"{album.album_name} -> {country}")
            else:
                failed.append(album.album_name)

        print(f"\nrelease_country backfilled for {len(filled)} album(s):")
        for line in filled:
            print(f"  {line}")

        if unavailable:
            print(
                f"\nNo country available from MusicBrainz for "
                f"{len(unavailable)} album(s):"
            )
            for name in unavailable:
                print(f"  {name}")

        if failed:
            print(f"\nFailed to resolve/write country for {len(failed)} album(s):")
            for name in failed:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
