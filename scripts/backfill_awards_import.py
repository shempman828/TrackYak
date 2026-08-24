"""
One-time backfill for awards data on entities matched to MusicBrainz before
the awards-import feature existed. Going forward, awards import happens
live, inline, whenever an album/artist gets newly MB-matched (see
src/awards/award_series_import.py's module docstring) -- this script is
only for the existing library's backlog of already-matched entities that
never got that treatment.

Run scripts/backfill_album_release_group_mbid.py FIRST for the most
complete album-level award coverage -- this script only finds
release-group-typed (album-level) awards for albums that already have
release_group_MBID set. Running this script without that backfill first
still works fine for Artist- and Recording-typed awards; it just leaves
album-level awards for the previously-matched backlog until that other
script (or a fresh MB match) fills in release_group_MBID.

Scoped to first_pass=1 for Album and Artist (reviewed rows only, matching
the release_group_MBID backfill's scope) -- but NOT for Track: confirmed
against the real DB that first_pass=1 AND MBID-set tracks number zero, so
that scope would silently skip every track. Track is scoped to "has an
MBID" instead.

Run once, manually, from the repo root, against a backed-up DB (this
script backs itself up first, same as the other backfill scripts):

    python scripts/backfill_awards_import.py
"""

import shutil
from datetime import datetime

from sqlalchemy.orm import scoped_session, sessionmaker

from src.awards.award_series_import import sync_awards
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase

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


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:

        def _progress(checked, total):
            print(f"  {checked}/{total} entities checked...")

        stats = sync_awards(
            controller.get.session, progress_callback=_progress, first_pass_only=True
        )

        print(f"\nEntities checked: {stats.entities_checked}")
        print(f"Awards created: {stats.awards_created}")
        print(f"Associations created: {stats.associations_created}")
        if stats.lookup_failures:
            print(f"Lookup failures: {stats.lookup_failures} (see log for details)")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
