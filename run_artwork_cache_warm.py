"""
run_artwork_cache_warm.py

Standalone script to pre-populate the album art thumbnail cache
(images/imagecache/artwork_cache.db) for every album in the library, so
the first real app launch after switching the UI over to the cache isn't
stuck cold-reading every track's embedded art during a grid render.

No GUI, no media player, no config - mirrors run_artwork_reconcile.py's
_MinimalController pattern.

Usage:
    python run_artwork_cache_warm.py
    python run_artwork_cache_warm.py --limit 200
"""

import argparse
import time

from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import MusicDatabase
from src.image.artwork_cache import ArtworkCache


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only warm the first N albums (for a quick sample run).",
    )
    args = parser.parse_args()

    controller = _MinimalController()
    cache = ArtworkCache()
    try:
        albums = controller.get.get_all_entities("Album")
        if not albums:
            print("No albums found.")
            return
        if args.limit is not None:
            albums = albums[: args.limit]

        total = len(albums)
        before_size = cache.db_path.stat().st_size if cache.db_path.exists() else 0

        thumbnails_generated = 0
        albums_with_no_art = 0
        start = time.time()

        for i, album in enumerate(albums, start=1):
            if i % 500 == 0:
                elapsed = time.time() - start
                logger.info(f"ArtworkCacheWarm: {i}/{total} albums processed ({elapsed:.0f}s)…")

            found_any = False
            for role in ("front", "rear", "liner"):
                if cache.has_art(album, role):
                    thumbnails_generated += 1
                    found_any = True
            if not found_any:
                albums_with_no_art += 1

        elapsed = time.time() - start
        after_size = cache.db_path.stat().st_size if cache.db_path.exists() else 0

        print("\nCache warm complete.")
        print(f"  albums processed: {total}")
        print(f"  thumbnails generated (or already cached): {thumbnails_generated}")
        print(f"  albums with no art found: {albums_with_no_art}")
        print(f"  elapsed: {elapsed:.1f}s")
        print(f"  cache file size: {before_size / 1e6:.1f} MB -> {after_size / 1e6:.1f} MB")
        print(f"  cache file path: {cache.db_path}")

    finally:
        cache.close()
        controller.close_session()
        logger.info("Database session closed.")


if __name__ == "__main__":
    main()
