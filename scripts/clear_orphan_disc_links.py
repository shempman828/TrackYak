"""
One-time repair: clear dangling Track.disc_id links.

"Detach from primary album" (AlbumsTab._remove_primary_album) used to set
tracks.album_id = NULL without touching tracks.disc_id. The disc still
belongs to the old album (discs.album_id is NOT NULL), so the track stayed
half-attached: gone from Album.tracks (joined on album_id) but still
present in Album.discs -> Disc.tracks. That stale half:

  * makes "Remove album art" / "Choose art" skip the track's file (the
    embed pass only walked Album.tracks), so its embedded picture is never
    cleared - and later resurfaces as cover art on whatever album the
    track is added to next;
  * throws off Album duration / track count / rating / genre + credit
    trickle-down, all of which read Album.tracks.

AlbumsTab now clears a stale disc_id in the same write as the album
change. This script fixes the rows already in that state: every track with
album_id IS NULL whose disc belongs to a real album gets disc_id = NULL
(fully detached - these were cut from their albums deliberately; the disc
link is just leftover).

Albums / discs left with zero tracks afterwards are reported, not deleted.

Dry run by default (reports, touches nothing). Pass --apply to write; a
timestamped copy of the DB is made next to it first.

Run from the repo root:

    python scripts/clear_orphan_disc_links.py            # dry run
    python scripts/clear_orphan_disc_links.py --apply
"""

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import shutil
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import Base  # noqa: F401  (registers every ORM model / mapper)
from src.foundation.logger_config import logger

DB_PATH = "music_library.db"

_ORPHAN_QUERY = text(
    """
    SELECT t.track_id, t.track_file_path, d.disc_id, d.album_id, a.album_name
    FROM tracks t
    JOIN discs d  ON t.disc_id = d.disc_id
    LEFT JOIN albums a ON a.album_id = d.album_id
    WHERE t.album_id IS NULL AND d.album_id IS NOT NULL
    ORDER BY d.album_id, t.track_id
    """
)


class _MinimalController:
    """Just enough of the DB layer for update_entities + its dirty-tracking.

    Deliberately a bare create_engine, NOT MusicDatabase: the latter runs
    the app's one-time startup migrations/backfills (album_gain/peak,
    release-type casing, ...) as a side effect of connecting, which we
    don't want a targeted data patch to trigger."""

    def __init__(self, db_path: str = DB_PATH):
        self.engine = create_engine(f"sqlite:///{db_path}")
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))
        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)


def _backup_db(db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{db_path}.pre-orphan-disc-{stamp}.bak"
    shutil.copy2(db_path, dest)
    return dest


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

    controller = _MinimalController(args.db)
    session = controller.get.session

    rows = session.execute(_ORPHAN_QUERY).fetchall()
    if not rows:
        print("No dangling Track.disc_id links found. Nothing to do.")
        return 0

    by_album: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_album[(r.album_id, r.album_name)].append(r)

    print(f"{len(rows)} track(s) with a dangling disc_id across {len(by_album)} album(s):\n")
    for (album_id, album_name), items in by_album.items():
        direct = session.execute(
            text("SELECT COUNT(*) FROM tracks WHERE album_id = :aid"), {"aid": album_id}
        ).scalar_one()
        note = "  <- album will have 0 tracks" if direct == 0 else f"  ({direct} track(s) remain)"
        print(f"  album {album_id} — {album_name!r}{note}")
        for r in items:
            print(f"      track {r.track_id}: {r.track_file_path}")
    print()

    if not args.apply:
        print("Dry run - no changes written. Re-run with --apply to clear these disc_id values.")
        return 0

    backup = _backup_db(args.db)
    print(f"DB backed up to {backup}")

    track_ids = [r.track_id for r in rows]
    if not controller.update.update_entities("Track", track_ids, disc_id=None):
        print("UPDATE failed - DB left unchanged (restore from the backup if needed).")
        return 1

    remaining = session.execute(_ORPHAN_QUERY).fetchall()
    empty_discs = session.execute(
        text(
            "SELECT d.disc_id, d.album_id FROM discs d "
            "LEFT JOIN tracks t ON t.disc_id = d.disc_id "
            "WHERE t.track_id IS NULL"
        )
    ).fetchall()

    print(f"Cleared disc_id on {len(track_ids)} track(s). {len(remaining)} dangling link(s) left.")
    if empty_discs:
        print(
            f"{len(empty_discs)} disc row(s) now have no tracks "
            f"(left in place, not deleted): "
            + ", ".join(f"disc {d.disc_id}/album {d.album_id}" for d in empty_discs)
        )
    logger.info("clear_orphan_disc_links: cleared disc_id on %d tracks", len(track_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
