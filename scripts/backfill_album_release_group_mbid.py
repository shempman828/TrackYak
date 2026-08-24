"""
One-time backfill for the new Album.release_group_MBID column (awards
import feature).

Album-level MusicBrainz award categories (Album of the Year, Best Rock
Album, etc.) are typed against a release's *release-group* MBID, not the
release MBID this app already stores in Album.MBID. For every Album row
that's been reviewed (first_pass=1) and already has an MBID, look up its
release-group MBID directly on MusicBrainz and fill it in.

Only ever fills a currently-NULL release_group_MBID -- never overwrites a
value already on file (e.g. one set by the ordinary album-review flow since
this column shipped).

Run once, manually, from the repo root, against a backed-up DB (this script
backs itself up first, same as scripts/backfill_album_release_country.py):

    python scripts/backfill_album_release_group_mbid.py
"""

import shutil
from datetime import datetime

import musicbrainzngs
from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
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
    backup_path = f"{db_path}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _fetch_release_group_mbid(release_mbid: str) -> str | None:
    """Direct MusicBrainz release lookup for the parent release-group MBID
    only -- includes=["release-groups"] avoids a full release-detail fetch."""
    configure()
    try:
        result = musicbrainzngs.get_release_by_id(
            release_mbid, includes=["release-groups"]
        )
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(f"Could not fetch release {release_mbid}: {e}")
        return None
    release_group = (result.get("release") or {}).get("release-group") or {}
    return release_group.get("id") or None


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        albums = controller.get.get_all_entities("Album") or []
        pending = [
            a
            for a in albums
            if a.first_pass and a.MBID and not a.release_group_MBID
        ]
        print(
            f"{len(pending)} reviewed album(s) have an MBID and no "
            "release_group_MBID on file."
        )

        filled, unavailable, failed = [], [], []

        for album in pending:
            try:
                release_group_mbid = _fetch_release_group_mbid(album.MBID)
            except MusicBrainzLookupError as e:
                logger.warning(
                    f"Could not resolve release-group for {album.album_name!r} "
                    f"({album.MBID}): {e}"
                )
                failed.append(album.album_name)
                continue

            if not release_group_mbid:
                unavailable.append(album.album_name)
                continue

            ok = controller.update.update_entity(
                "Album", album.album_id, release_group_MBID=release_group_mbid
            )
            if ok:
                filled.append(f"{album.album_name} -> {release_group_mbid}")
            else:
                failed.append(album.album_name)

        print(f"\nrelease_group_MBID backfilled for {len(filled)} album(s):")
        for line in filled:
            print(f"  {line}")

        if unavailable:
            print(
                f"\nNo release-group available from MusicBrainz for "
                f"{len(unavailable)} album(s):"
            )
            for name in unavailable:
                print(f"  {name}")

        if failed:
            print(f"\nFailed to resolve/write release-group for {len(failed)} album(s):")
            for name in failed:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
