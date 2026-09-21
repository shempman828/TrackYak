from collections import defaultdict

from PySide6.QtCore import Signal
from sqlalchemy import select

from src.common.cancellable_worker import CancellableWorker
from src.db.db_tables import TrackGenre
from src.foundation.logger_config import logger


class GenreLoaderWorker(CancellableWorker):
    """Background-thread worker that fetches all genres and their direct/recursive track counts."""

    # Payload: (genres, direct_counts_by_genre_id, recursive_counts_by_genre_id)
    # Uses `object` rather than `list`/`dict`, matching RoleLoaderWorker --
    # PySide6's queued cross-thread delivery can otherwise fail to
    # copy-convert plain dict/list signal args.
    finished = Signal(object, object, object)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            genres = self.controller.get.get_all_entities("Genre") or []

            direct_track_ids = defaultdict(set)
            for genre_id, track_id in self.controller.get.session.execute(select(TrackGenre.genre_id, TrackGenre.track_id)).all():
                direct_track_ids[genre_id].add(track_id)

            direct_counts = {genre_id: len(track_ids) for genre_id, track_ids in direct_track_ids.items()}

            children_map = defaultdict(list)
            for genre in genres:
                children_map[genre.parent_id].append(genre.genre_id)

            recursive_track_ids: dict = {}

            # Recursive counts are computed with one ungrouped TrackGenre query
            # plus a memoized bottom-up union of per-genre track-id sets over
            # the parent/child structure, instead of Genre.all_track_count's
            # per-object Python recursion (no batching, O(n^2)/N+1-query risk).
            # Sets (not a plain sum) are required because a track tagged with
            # both a genre and one of its descendants must only count once.
            def track_ids_for(genre_id, visiting=frozenset()):
                if genre_id in recursive_track_ids:
                    return recursive_track_ids[genre_id]
                if genre_id in visiting:
                    # Cyclic parent_id chain (shouldn't happen); break the
                    # cycle instead of recursing forever.
                    return set()
                visiting = visiting | {genre_id}
                ids = set(direct_track_ids.get(genre_id, ()))
                for child_id in children_map.get(genre_id, []):
                    ids |= track_ids_for(child_id, visiting)
                recursive_track_ids[genre_id] = ids
                return ids

            for genre in genres:
                track_ids_for(genre.genre_id)

            recursive_totals = {genre_id: len(track_ids) for genre_id, track_ids in recursive_track_ids.items()}

        except Exception as e:
            logger.exception("Genre count scan failed")
            self.error.emit(str(e))
            self._release_db_session()
            return

        self._release_db_session()
        self.finished.emit(genres, direct_counts, recursive_totals)
