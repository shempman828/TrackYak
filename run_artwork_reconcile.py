"""
run_artwork_reconcile.py

Standalone script to run the FLAC artwork reconciliation pass without
starting the full application (no GUI, no media player, no config).

Defaults to dry_run=True (nothing is written). Pass --apply to actually
write/strip embedded art.

Usage:
    python run_artwork_reconcile.py               # dry run
    python run_artwork_reconcile.py --apply        # writes for real
    python run_artwork_reconcile.py --audit-log path/to/audit.json
"""

import argparse

from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import MusicDatabase
from src.library.library_artwork_reconcile import ArtworkReconciler
from src.core.logger_config import logger


class _MinimalController:
    """Boots only the database layer of MusicController - same pattern as
    run_repair.py's _MinimalController, but goes through MusicDatabase first
    (same as run.py) so any ORM/schema drift (missing columns) gets the same
    safe auto-migration a normal app launch would apply, instead of every
    query on an affected table silently failing."""

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write/strip embedded art (default is dry-run).",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to write the full audit log as JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N tracks (for a quick sample run).",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    logger.info(f"=== Artwork Reconciliation: starting (dry_run={dry_run}) ===")

    controller = _MinimalController()
    try:
        reconciler = ArtworkReconciler(controller, dry_run=dry_run, limit=args.limit)
        summary = reconciler.run()

        logger.info("=== Artwork Reconciliation: finished ===")
        print("\nReconciliation complete. Summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")

        if args.audit_log:
            reconciler.export_audit_log(args.audit_log)
            print(f"\nAudit log written to: {args.audit_log}")
        print(f"\nAudit log entries: {len(reconciler.audit_log)}")

    finally:
        controller.close_session()
        logger.info("Database session closed.")


if __name__ == "__main__":
    main()
