"""
One-time repair: strip fabricated per-line timestamps from Track.lyrics.

The lyrics provider (lyriq) returns a Lyrics object whose ``lyrics`` dict is
always populated -- and when LRCLib only has *plain* lyrics for a track,
lyriq fabricates one key per line from the line index (``"00.00"``,
``"01.00"``, ...). The old ``_format_lyrics`` serialised that dict blindly,
so every plain-lyrics track searched from the Lyrics tab / Player Dock got
stored as::

    [00.00] First line
    [01.00] Second line
    [02.00] ...

i.e. fake sequential "timing" baked into the text. ``format_lyrics_for_storage``
now trusts the object's own ``synced_lyrics`` / ``plain_lyrics`` fields, so
new searches are clean. This script fixes the rows already written that way.

A row is healed only when it has NO real ``[m:ss]`` LRC line and three or
more lines carry the ``[NN.NN]`` placeholder prefix whose integers form a
non-decreasing run starting at 0. For those rows the ``[NN.NN] `` prefix is
removed from every line; nothing else is touched. Rows with genuine synced
lyrics are left alone.

Dry run by default (reports, touches nothing). Pass --apply to write; a
timestamped copy of the DB is made next to it first.

Run from the repo root:

    python scripts/heal_fake_synced_lyrics.py            # dry run
    python scripts/heal_fake_synced_lyrics.py --apply
"""

import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import Base  # noqa: F401  (registers every ORM model / mapper)
from src.foundation.logger_config import logger

DB_PATH = "music_library.db"

# A fabricated placeholder prefix: "[NN.NN] " at the very start of a line.
_FAKE_PREFIX = re.compile(r"^\[(\d{1,3})\.\d{2}\][ \t]?")
# A genuine LRC timestamp uses a colon between minutes and seconds.
_REAL_LRC = re.compile(r"^\[\d{1,2}:\d{2}(?:[.,]\d+)?\]")


def _is_fake_timed(lyrics: str) -> bool:
    """True when *lyrics* carry lyriq's fabricated per-line placeholders and
    no real synced line.

    Signal: no genuine ``[m:ss]`` LRC line anywhere, and the ``[NN.NN]``
    placeholder prefix covers effectively every non-blank line (>= 70%, and
    at least three of them). Real lyrics never stamp every line ``[07.00]``.
    """
    stripped = [ln.strip() for ln in lyrics.splitlines()]
    if any(_REAL_LRC.match(ln) for ln in stripped):
        return False
    non_blank = [ln for ln in stripped if ln]
    fake = [ln for ln in non_blank if _FAKE_PREFIX.match(ln)]
    return len(fake) >= 3 and len(fake) >= 0.7 * len(non_blank)


def _heal(lyrics: str) -> str:
    """Drop the ``[NN.NN] `` prefix from every line.

    The prefix integer is the original line index (lyriq builds it from
    ``enumerate``), so for tracks whose keys got string-sorted on the way in
    -- "10.00" < "100.00" < "11.00" -- sorting by that integer first also
    restores the scrambled line order. Lines with no prefix keep their spot
    relative to their neighbours.
    """
    out, buf = [], []

    def _flush():
        buf.sort(key=lambda pair: pair[0])
        out.extend(text for _, text in buf)
        buf.clear()

    for ln in lyrics.splitlines():
        m = _FAKE_PREFIX.match(ln)
        if m:
            buf.append((int(m.group(1)), _FAKE_PREFIX.sub("", ln)))
        else:
            _flush()
            out.append(ln)
    _flush()
    return "\n".join(out)


class _MinimalController:
    """Just enough of the DB layer for update_entity + its dirty-tracking.

    Deliberately a bare create_engine, NOT MusicDatabase: the latter runs the
    app's one-time startup migrations/backfills as a side effect of
    connecting, which we don't want a targeted data patch to trigger."""

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
    dest = f"{db_path}.pre-fake-lyrics-{stamp}.bak"
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

    rows = session.execute(
        text("SELECT track_id, lyrics FROM tracks WHERE lyrics IS NOT NULL AND lyrics != ''")
    ).fetchall()

    targets = []
    for r in rows:
        if _is_fake_timed(r.lyrics):
            healed = _heal(r.lyrics)
            if healed != r.lyrics:
                targets.append((r.track_id, r.lyrics, healed))

    if not targets:
        print("No tracks with fabricated per-line lyric timestamps found. Nothing to do.")
        return 0

    print(f"{len(targets)} track(s) with fabricated per-line lyric timestamps:\n")
    for tid, before, after in targets:
        b0 = before.splitlines()[0] if before.splitlines() else ""
        a0 = after.splitlines()[0] if after.splitlines() else ""
        print(f"  track {tid}: {b0!r}  ->  {a0!r}")
    print()

    if not args.apply:
        print("Dry run - no changes written. Re-run with --apply to strip these prefixes.")
        return 0

    backup = _backup_db(args.db)
    print(f"DB backed up to {backup}")

    failed = []
    for tid, _before, after in targets:
        if not controller.update.update_entity("Track", tid, lyrics=after):
            failed.append(tid)

    healed = len(targets) - len(failed)
    print(f"Stripped fabricated timestamps from {healed} track(s).")
    if failed:
        print(f"FAILED on {len(failed)} track(s): {failed} (restore from the backup if needed).")
    logger.info("heal_fake_synced_lyrics: healed %d tracks, %d failed", healed, len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
