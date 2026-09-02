"""
One-time backfill for the new MoodTrackAssociation.score column
(docs/specs/mood_representative_tracks.md).

`score` is the mood's lyrics-match density (keyword hits / total lyric
tokens) recorded when a track is auto-tagged. Rows that predate the
column -- and any hand-added mood tags, which the auto-tag write path
never scores -- have `score NULL` and are invisible to the "most
representative tracks per mood" statistic until this runs.

For every mood_track_association row with `score IS NULL` whose track has
non-empty lyrics, this sets `score` to that mood's density, or `0.0` when
the mood's keyword list doesn't match the lyrics at all. Rows whose track
has no lyrics are left `NULL` (nothing to score).

Only ever fills a currently-NULL score -- never recomputes or overwrites a
value already on file. Safe to run more than once; the second run is a
no-op.

Run once, manually, from the repo root:

    python scripts/backfill_mood_association_scores.py
"""

from datetime import datetime
import os
import shutil
import sys

from src.db.db_tables.database import MusicDatabase
from src.db.db_tables.mood import MoodTrackAssociation
from src.db.db_tables.track import Track
from src.foundation.logger_config import logger
from src.mood.mood_scoring import score_moods_detailed

DB_PATH = "music_library.db"
PROGRESS_EVERY = 2000


def _backup(db_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("backups", exist_ok=True)
    backup_path = f"backups/{os.path.basename(db_path)}.{timestamp}.moodscore.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def backfill_scores(session, progress_every: int = PROGRESS_EVERY) -> tuple[int, int]:
    """Fill `score` on every NULL-score association whose track has lyrics.

    Returns (rows_scored, rows_keyword_matched). A row whose track has no
    lyrics is left untouched (NULL). Commits once at the end.
    """
    rows = (
        session.query(MoodTrackAssociation)
        .join(Track, MoodTrackAssociation.track_id == Track.track_id)
        .filter(
            MoodTrackAssociation.score.is_(None),
            Track.lyrics.isnot(None),
            Track.lyrics != "",
        )
        .all()
    )
    total = len(rows)
    if not total:
        return 0, 0

    # Score each track's lyrics once, not once per (mood, track) row.
    detail_by_track: dict[int, dict] = {}
    matched = 0
    for i, assoc in enumerate(rows, start=1):
        detail = detail_by_track.get(assoc.track_id)
        if detail is None:
            detail = score_moods_detailed(assoc.track.lyrics)
            detail_by_track[assoc.track_id] = detail
        hit = detail.get(assoc.mood.mood_name)
        assoc.score = hit.density if hit else 0.0
        if hit:
            matched += 1
        if progress_every and i % progress_every == 0:
            print(f"  scored {i}/{total}...")

    session.commit()
    return total, matched


def main():
    if not os.path.exists(DB_PATH):
        sys.exit(f"{DB_PATH} not found -- run this from the repo root.")

    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    # Instantiating MusicDatabase runs the schema integrity pass, which
    # ALTERs the `score` column in if it isn't there yet.
    db = MusicDatabase(f"sqlite:///{DB_PATH}")

    session = db.Session()
    try:
        total, matched = backfill_scores(session)
        if not total:
            print("No NULL-score rows with lyrics to backfill -- nothing to do.")
            return
        logger.info(
            f"Backfilled mood-match score for {total} association(s) "
            f"({matched} keyword-matched, {total - matched} set to 0.0)."
        )
        print(
            f"\nDone: {total} row(s) scored "
            f"({matched} keyword-matched, {total - matched} set to 0.0)."
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
