"""
album_art_worker.py — background worker that resolves album-art cache
misses for the album view's Art filter without blocking the UI thread.
"""

import sqlite3

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class ArtCacheWorker(CancellableWorker):
    """
    Warms the ArtworkCache row for albums whose entry is missing or stale,
    by calling ArtworkCache.get_dimensions() - which internally reads and
    decodes the embedded image and populates the row for every role in one
    pass - off the UI thread. AlbumView only hands it albums that
    ArtworkCache.peek_has_art()/peek_dimensions() couldn't answer
    synchronously.

    Emits `resolved` once per album as its cache row becomes warm, so the
    view can re-peek it (now a cheap, confirmed cache hit) to decide
    whether it belongs in the filtered/sorted grid, instead of blocking
    until every pending album has been processed.
    """

    resolved = Signal(int)  # album_id

    def __init__(self, albums: list, cache, role: str = "front"):
        super().__init__()
        self._albums = albums
        self._cache = cache
        self._role = role

    def run(self):
        for album in self._albums:
            if self.is_cancelled:
                break
            try:
                self._cache.get_dimensions(album, self._role)
            except sqlite3.Error as e:
                logger.warning(
                    f"ArtCacheWorker: get_dimensions failed for album "
                    f"{getattr(album, 'album_id', '?')}: {e}"
                )
            self.resolved.emit(album.album_id)
