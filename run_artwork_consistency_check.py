"""
run_artwork_consistency_check.py

Standalone script that checks whether every embeddable (FLAC/MP3) track in
each album agrees on its embedded art per role (front/rear/liner). This is
a prerequisite for the thumbnail cache, which reads a single representative
track per album/role and assumes the rest agree.

Read-only by default: just reports conflicts. Pass --resolve plus --apply
to fix specific conflicts by picking which track's art should win.

Usage:
    python run_artwork_consistency_check.py
    python run_artwork_consistency_check.py --report path/to/report.json
    python run_artwork_consistency_check.py --apply \
        --resolve 123:front=456 --resolve 123:rear=456
"""

import argparse

from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import MusicDatabase
from src.library.library_artwork_consistency import ArtworkConsistencyChecker


class _MinimalController:
    """Boots only the database layer - same pattern as
    run_artwork_reconcile.py's _MinimalController."""

    def __init__(self):
        db = MusicDatabase("sqlite:///music_library.db")
        self.engine = db.engine
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

    def close_session(self):
        self.SessionFactory.remove()


def _parse_resolve_arg(raw: str):
    """Parse "ALBUM_ID:ROLE=TRACK_ID" into (album_id, role, track_id)."""
    try:
        album_part, rest = raw.split(":", 1)
        role, track_part = rest.split("=", 1)
        return int(album_part), role, int(track_part)
    except (ValueError, IndexError) as e:
        raise argparse.ArgumentTypeError(
            f"Invalid --resolve value {raw!r}, expected ALBUM_ID:ROLE=TRACK_ID"
        ) from e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the --resolve decisions (default is report-only).",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to write the full conflict report as JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only scan the first N albums (for a quick sample run).",
    )
    parser.add_argument(
        "--resolve",
        action="append",
        default=None,
        type=_parse_resolve_arg,
        metavar="ALBUM_ID:ROLE=TRACK_ID",
        help="Resolve a specific conflict by picking the winning track "
        "(repeatable). Requires --apply.",
    )
    args = parser.parse_args()

    controller = _MinimalController()
    try:
        checker = ArtworkConsistencyChecker(controller, limit=args.limit)
        summary = checker.run()

        print("\nConsistency check complete. Summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")

        if args.report:
            checker.export_report(args.report)
            print(f"\nReport written to: {args.report}")

        if checker.conflicts:
            print(f"\n{len(checker.conflicts)} conflict(s) found:")
            for c in checker.conflicts:
                print(
                    f"  album_id={c['album_id']} ({c['album_name']!r}) "
                    f"role={c['role']}: "
                    + ", ".join(
                        f"track {t['track_id']} ({t['track_path']}) "
                        f"hash={t['hash'][:12] if t['hash'] else None}"
                        for t in c["tracks"]
                    )
                )

        if not args.resolve:
            return

        if not args.apply:
            print("\n--resolve given without --apply; nothing written.")
            return

        resolved_lookup = {
            (album_id, role): track_id for album_id, role, track_id in args.resolve
        }
        for (album_id, role), winning_track_id in resolved_lookup.items():
            album = controller.get.get_entity_object("Album", album_id=album_id)
            if album is None:
                logger.error(f"No such album_id={album_id}, skipping --resolve entry.")
                continue
            print(f"\nResolving album_id={album_id} role={role} -> track {winning_track_id}")
            failed = checker.resolve_conflict(album, role, winning_track_id)
            if failed:
                print(f"  {len(failed)} track(s) failed to update:")
                for p in failed:
                    print(f"    {p}")
            else:
                print("  done, all tracks updated.")

    finally:
        controller.close_session()
        logger.info("Database session closed.")


if __name__ == "__main__":
    main()
