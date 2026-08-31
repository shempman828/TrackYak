"""
One-time cleanup for files left behind in images/artist_images/ and
images/publisher_logos/ after the entity that owned them was deleted, or
after its picture was cleared / re-picked with a different extension in an
editor. Nothing removed these before DeleteDB / MergeDB grew their
delete- and merge-time hooks.

Deletes every file in those two directories whose filename is referenced by
no Artist.profile_pic_path / Publisher.logo_path row. Files a row points at
that are missing on disk are reported, never touched. The database is only
read.

Guard: if a model has zero referenced images but its directory holds files,
that directory is skipped (a partial DB read must not wipe the folder).

Dry run by default. Pass --apply to actually unlink.

    python scripts/prune_orphaned_entity_images.py            # dry run
    python scripts/prune_orphaned_entity_images.py --apply
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.db_tables.database import MusicDatabase
from src.image.image_cleanup import prune_orphaned_images

DB_PATH = "music_library.db"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="unlink files (default: dry run)")
    parser.add_argument("--db", default=DB_PATH, help=f"library DB path (default: {DB_PATH})")
    args = parser.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"{args.db} not found -- run this from the repo root.")

    session = MusicDatabase(f"sqlite:///{args.db}").Session()
    try:
        result = prune_orphaned_images(session, dry_run=not args.apply)
    finally:
        session.close()

    removed = result["removed"]
    verb = "Removed" if args.apply else "Would remove"
    print(f"\n{verb} {len(removed)} orphaned image file(s):")
    for path in sorted(removed):
        print(f"  {path}")

    if result["missing_refs"]:
        print(f"\n{len(result['missing_refs'])} row(s) reference a file missing on disk:")
        for name in result["missing_refs"]:
            print(f"  {name}")

    if not args.apply:
        print("\nDry run -- pass --apply to unlink.")


if __name__ == "__main__":
    main()
