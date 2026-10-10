"""Background workers that warm the album-art cache and embed covers into track files."""

from pathlib import Path
import sqlite3

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.image.artwork_cache import pick_representative_track
from src.metadata.readers.metadata_artwork import ArtworkExtractor


class ArtCacheWorker(CancellableWorker):
    """Warms the ArtworkCache row for albums whose entry is missing or stale, off the UI thread."""

    # Emitted per album only once its row is confirmed (a hit or a confirmed
    # miss), so the view's re-peek can't misclassify an unknown row as "no art".
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
            if self._cache.is_degraded():
                # Each remaining album would just fail its write; a later pass picks them up.
                logger.debug("ArtCacheWorker: cache is read-only, stopping warm pass")
                break
            # Yield to a foreground writer (the album editor's embed).
            self._cache.warmers_wait_if_paused(lambda: self.is_cancelled)
            if self.is_cancelled:
                break
            try:
                self._cache.get_dimensions(album, self._role)
            except sqlite3.Error as e:
                logger.warning(f"ArtCacheWorker: get_dimensions failed for album {getattr(album, 'album_id', '?')}: {e}")
                continue
            if self._cache.peek_has_art(album, self._role) is None:
                continue  # write was skipped or failed; row state is still unknown
            self.resolved.emit(album.album_id)


class CoverEmbedWorker(CancellableWorker):
    """Rewrites the embedded cover into every embeddable track of an album, off the UI thread."""

    completed = Signal(list, object)  # failed_paths, (w, h) of the stored image or None
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
                return  # callers cancel only while closing, so no signal is needed
            dims = None
            if self._cache is not None:
                rep = pick_representative_track(self._album)
                if rep is not None and rep.track_file_path in failed:
                    # The cache keys on this file's old mtime; storing the new art
                    # would show art that the file does not hold.
                    self._cache.invalidate(self._album.album_id, self._role)
                else:
                    self._cache.store(self._album, self._role, self._image_bytes)
                    if self._image_bytes is not None:
                        dims = self._cache.get_dimensions(self._album, self._role)
            self.completed.emit(failed, dims)
        except Exception as e:
            # Boundary catch: an escaping exception on this thread is lost and
            # leaves the editor's buttons disabled forever.
            logger.exception("CoverEmbedWorker failed")
            self.error.emit(str(e))
        finally:
            if self._cache is not None:
                self._cache.resume_warmers()

    def _embed_to_tracks(self):
        """Embed (or strip, if image_bytes is None) the role in every embeddable track; return failed paths."""
        failed = []
        for track in self._tracks:
            if self.is_cancelled:
                break
            file_path = getattr(track, "track_file_path", None)
            if not file_path or Path(file_path).suffix.lower() not in self._EMBEDDABLE_EXTENSIONS:
                continue
            try:
                success = self._writer.write_artwork_to_file(file_path, self._role, self._image_bytes)
            except (ValueError, OSError) as e:
                # A locked, vanished or full-disk file fails only this track, not the album.
                logger.error(f"Error embedding {self._role} cover into {file_path}: {e}")
                success = False
            if not success:
                failed.append(file_path)
        return failed
