"""
Regression tests for ArtCacheWorker / CoverEmbedWorker, calling .run()
synchronously rather than through a real QThread/event loop.

Motivating bugs:
- CoverEmbedWorker only caught ValueError around a per-track write, so an
  OSError (locked/permission-denied/vanished/disk-full file) escaped the
  per-track loop and aborted the whole album's embed pass instead of
  recording just that track as failed.
- ArtCacheWorker emitted `resolved` even when get_dimensions() raised, so a
  transient cache-write failure silently misclassified the album's "has
  art" status with no retry.
"""

import sqlite3

from src.album.album_art_worker import ArtCacheWorker, CoverEmbedWorker


class _Album:
    def __init__(self, album_id):
        self.album_id = album_id


class _Track:
    def __init__(self, path):
        self.track_file_path = path


class _FakeCache:
    def __init__(self, dims_by_album=None, raise_for=None):
        self._dims_by_album = dims_by_album or {}
        self._raise_for = raise_for or set()
        self.warmed = []

    def is_degraded(self):
        return False

    def warmers_wait_if_paused(self, is_cancelled):
        pass

    def get_dimensions(self, album, role):
        if album.album_id in self._raise_for:
            raise sqlite3.Error("db is locked")
        self.warmed.append(album.album_id)
        return self._dims_by_album.get(album.album_id, (True, None))

    def pause_warmers(self):
        pass

    def resume_warmers(self):
        pass

    def store(self, album, role, image_bytes):
        pass


class _FlakyWriter:
    """Raises OSError for one path, succeeds for the rest."""

    def __init__(self, bad_path):
        self._bad_path = bad_path

    def write_artwork_to_file(self, path, role, image_bytes):
        if path == self._bad_path:
            raise OSError("Permission denied")
        return True


def test_art_cache_worker_skips_resolved_emit_on_db_error():
    good = _Album(1)
    bad = _Album(2)
    cache = _FakeCache(raise_for={2})
    worker = ArtCacheWorker([good, bad], cache)

    resolved = []
    worker.resolved.connect(resolved.append)
    worker.run()

    assert resolved == [1]


def test_art_cache_worker_emits_resolved_on_success():
    album = _Album(1)
    cache = _FakeCache()
    worker = ArtCacheWorker([album], cache)

    resolved = []
    worker.resolved.connect(resolved.append)
    worker.run()

    assert resolved == [1]


def test_cover_embed_worker_records_os_error_as_failed_track_not_abort():
    tracks = [_Track("/music/good.flac"), _Track("/music/locked.flac")]
    writer = _FlakyWriter(bad_path="/music/locked.flac")
    worker = CoverEmbedWorker(
        album=_Album(1),
        tracks=tracks,
        cache=None,
        writer=writer,
        cover_type="front",
        image_bytes=b"fake-image-bytes",
    )

    completed = []
    errored = []
    worker.completed.connect(lambda failed, dims: completed.append((failed, dims)))
    worker.error.connect(errored.append)
    worker.run()

    assert errored == []
    assert completed == [(["/music/locked.flac"], None)]
