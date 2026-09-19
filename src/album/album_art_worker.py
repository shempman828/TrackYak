"""
album_art_worker.py — background workers for album art:

* ArtCacheWorker  — resolves album-art cache misses for the album view's
  Art filter without blocking the UI thread.
* CoverEmbedWorker — embeds (or strips) a freshly-picked cover into every
  track file of one album and warms the cache row, off the UI thread, so
  the album editor no longer freezes while it rewrites a dozen FLACs.
"""

from pathlib import Path
import sqlite3

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor


class ArtCacheWorker(CancellableWorker):
    """Warms the ArtworkCache row for albums whose entry is missing or stale, off the UI thread."""

    # Emits once per album as its cache row becomes warm, so the view can
    # re-peek it (now a cheap, confirmed cache hit) to decide whether it
    # belongs in the filtered/sorted grid, instead of blocking until every
    # pending album has been processed. Only emitted on a successful
    # get_dimensions() call (a hit or a confirmed miss) -- not after a
    # sqlite3.Error, since that leaves the cache row's state unknown rather
    # than confirmed, and emitting anyway would misclassify the album's
    # "has art" status with no retry.
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
            # The cache db briefly went read-only (see ArtworkCache
            # _DEGRADED_BACKOFF_SEC). Warming every remaining album now just
            # means a failed write apiece - stop and let a later warm pass
            # pick them up once the db is writable again.
            if getattr(self._cache, "is_degraded", lambda: False)():
                logger.debug("ArtCacheWorker: cache is read-only, stopping warm pass")
                break
            # Yield to a foreground writer (album editor embedding new art)
            # rather than queue a whole-library warm ahead of its writes on
            # the cache's single connection.
            self._cache.warmers_wait_if_paused(lambda: self.is_cancelled)
            if self.is_cancelled:
                break
            try:
                self._cache.get_dimensions(album, self._role)
            except sqlite3.Error as e:
                logger.warning(
                    f"ArtCacheWorker: get_dimensions failed for album "
                    f"{getattr(album, 'album_id', '?')}: {e}"
                )
                continue
            self.resolved.emit(album.album_id)


class CoverEmbedWorker(CancellableWorker):
    """Rewrites the embedded cover into every embeddable track of an album, off the UI thread."""

    # completed: fires on success with (failed_paths, dims) -- dims is the
    # (w, h) of the stored image, or None (e.g. on clear).
    completed = Signal(list, object)  # failed_paths, dims-or-None
    # error: fires with a message string if the embed/store raised.
    error = Signal(str)

    _EMBEDDABLE_EXTENSIONS = ArtworkExtractor.SUPPORTED_EXTENSIONS

    def __init__(self, album, tracks, cache, writer, cover_type, image_bytes):
        super().__init__()
        self._album = album
        self._tracks = tracks
        self._cache = cache
        self._writer = writer
        self._role = cover_type
        self._image_bytes = image_bytes

    def run(self):
        if self._cache is not None:
            self._cache.pause_warmers()
        try:
            failed = self._embed_to_tracks()
            if self.is_cancelled:
                return
            dims = None
            if self._cache is not None:
                self._cache.store(self._album, self._role, self._image_bytes)
                if self._image_bytes is not None:
                    dims = self._cache.get_dimensions(self._album, self._role)
            self.completed.emit(failed, dims)
        except Exception as e:
            # Broad boundary catch: this runs on a background thread and an
            # escaping exception (PIL decode failure, sqlite error) would
            # otherwise be lost silently and leave the editor's buttons
            # disabled forever.
            logger.exception("CoverEmbedWorker failed")
            self.error.emit(str(e))
        finally:
            if self._cache is not None:
                self._cache.resume_warmers()

    def _embed_to_tracks(self):
        """Embed (image_bytes given) or strip (None) the role into every
        FLAC/MP3 track. Returns the list of file paths that failed."""
        failed = []
        for track in self._tracks:
            if self.is_cancelled:
                break
            file_path = getattr(track, "track_file_path", None)
            if not file_path or Path(file_path).suffix.lower() not in self._EMBEDDABLE_EXTENSIONS:
                continue
            try:
                success = self._writer.write_artwork_to_file(
                    file_path, self._role, self._image_bytes
                )
            except (ValueError, OSError) as e:
                # Writers open/rewrite the file mid-call, so a locked,
                # permission-denied, vanished, or disk-full file raises
                # OSError here, not just ValueError -- catch both so one
                # bad track is recorded as failed instead of aborting the
                # whole album's embed pass via run()'s broad except.
                logger.error(f"Error embedding {self._role} cover into {file_path}: {e}")
                success = False
            if not success:
                failed.append(file_path)
        return failed
