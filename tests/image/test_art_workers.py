"""Album-art workers: the art-cache resolve worker, the cover-embed worker,
and warmer pausing.
"""

import sqlite3
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop, QTimer
import pytest

from src.image.album_art_worker import ArtCacheWorker, CoverEmbedWorker
from src.image.artwork_cache import ArtworkCache

# ---------------------------------------------------------------------------
# Regression tests for ArtCacheWorker / CoverEmbedWorker, calling .run()
# ---------------------------------------------------------------------------


class _Album:
    def __init__(self, album_id):
        self.album_id = album_id


class _Track:
    def __init__(self, path):
        self.track_file_path = path


class _FakeCache:
    def __init__(self, dims_by_album=None, raise_for=None, unknown=None):
        self._dims_by_album = dims_by_album or {}
        self._raise_for = raise_for or set()
        self._unknown = unknown or set()
        self.invalidated = []
        self.stored = []
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

    def peek_has_art(self, album, role):
        return None if album.album_id in self._unknown else True

    def pause_warmers(self):
        pass

    def resume_warmers(self):
        pass

    def store(self, album, role, image_bytes):
        self.stored.append(role)

    def invalidate(self, album_id, role=None):
        self.invalidated.append((album_id, role))


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
    worker = CoverEmbedWorker(album=_Album(1), tracks=tracks, cache=None, writer=writer, cover_type="front", image_bytes=b"fake-image-bytes")

    completed = []
    errored = []
    worker.completed.connect(lambda failed, dims: completed.append((failed, dims)))
    worker.error.connect(errored.append)
    worker.run()

    assert errored == []
    assert completed == [(["/music/locked.flac"], None)]


def test_art_cache_worker_skips_resolved_emit_when_row_still_unknown():
    cache = _FakeCache(unknown={2})
    worker = ArtCacheWorker([_Album(1), _Album(2)], cache)

    resolved = []
    worker.resolved.connect(resolved.append)
    worker.run()

    assert resolved == [1]


def test_cover_embed_worker_invalidates_instead_of_storing_when_representative_track_fails():
    from types import SimpleNamespace

    rep = SimpleNamespace(track_id=1, track_file_path="/music/01.flac", disc_id=None, track_number=1)
    other = SimpleNamespace(track_id=2, track_file_path="/music/02.flac", disc_id=None, track_number=2)
    album = SimpleNamespace(album_id=7, tracks=[rep, other], discs=[])
    cache = _FakeCache()
    worker = CoverEmbedWorker(album, [rep, other], cache, _FlakyWriter(bad_path=rep.track_file_path), "front", b"img")

    completed = []
    worker.completed.connect(lambda failed, dims: completed.append((failed, dims)))
    worker.run()

    assert completed == [([rep.track_file_path], None)]
    assert cache.invalidated == [(7, "front")]
    assert cache.stored == []


# ---------------------------------------------------------------------------
# Regression: adding/clearing album art must not run on the Qt UI thread.
# ---------------------------------------------------------------------------


MAIN_IDENT = threading.get_ident()


class FakeCache:
    """Records pause/resume ordering; stands in for the real ArtworkCache."""

    def __init__(self, dims=(111, 222)):
        self.calls = []
        self.stored = []
        self._dims = dims

    def pause_warmers(self):
        self.calls.append("pause")

    def resume_warmers(self):
        self.calls.append("resume")

    def store(self, album, role, data):
        self.calls.append("store")
        self.stored.append((role, data))

    def get_dimensions(self, album, role):
        return self._dims


def _wait_for_completion(worker, timeout_ms=5000):
    """Spin a local event loop until the worker emits completed/error."""
    loop = QEventLoop()
    result = {}
    worker.completed.connect(
        lambda failed, dims: (result.update(failed=failed, dims=dims), loop.quit())
    )
    worker.error.connect(lambda msg: (result.update(error=msg), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    worker.wait(2000)
    return result


def _flac_tracks(n):
    return [SimpleNamespace(track_file_path=f"/music/t{i}.flac") for i in range(n)]


# -- AC1 --------------------------------------------------------------------
def test_embed_runs_off_calling_thread_and_start_is_nonblocking(qapp):
    gate = threading.Event()
    write_idents = []

    class BlockingWriter:
        def write_artwork_to_file(self, file_path, role, data):
            write_idents.append(threading.get_ident())
            gate.wait(2.0)  # park so the calling thread can observe non-blocking start
            return True

    cache = FakeCache()
    worker = CoverEmbedWorker(
        object(), _flac_tracks(3), cache, BlockingWriter(), "front", b"IMGBYTES"
    )

    t0 = time.perf_counter()
    worker.start()
    start_elapsed = time.perf_counter() - t0

    # start() must not have blocked on the (still-parked) first write.
    assert start_elapsed < 0.2
    time.sleep(0.05)
    assert worker.isRunning()

    gate.set()
    result = _wait_for_completion(worker)

    assert result.get("failed") == []
    assert result.get("dims") == (111, 222)
    assert len(write_idents) == 3
    assert all(ident != MAIN_IDENT for ident in write_idents)
    # Warmers paused for the duration, resumed exactly once afterwards.
    assert cache.calls == ["pause", "store", "resume"]
    assert cache.stored == [("front", b"IMGBYTES")]


# -- AC2 --------------------------------------------------------------------
def test_write_failures_reported_and_non_embeddable_skipped(qapp):
    class PartialWriter:
        def write_artwork_to_file(self, file_path, role, data):
            return not file_path.endswith("t1.flac")  # t1 fails

    tracks = _flac_tracks(3) + [SimpleNamespace(track_file_path="/music/cover.txt")]
    worker = CoverEmbedWorker(
        object(), tracks, FakeCache(), PartialWriter(), "rear", b"X"
    )
    worker.start()
    result = _wait_for_completion(worker)

    assert result.get("failed") == ["/music/t1.flac"]  # .txt never attempted


# -- AC3 --------------------------------------------------------------------
def test_artcacheworker_holds_while_warmers_paused(qapp, tmp_path):
    cache = ArtworkCache(str(tmp_path / "artcache.db"))
    cache.pause_warmers()

    albums = [SimpleNamespace(album_id=i, tracks=[]) for i in range(5)]
    resolved = []
    worker = ArtCacheWorker(albums, cache, "front")
    worker.resolved.connect(resolved.append)
    worker.start()

    deadline = time.time() + 0.4
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert resolved == []  # paused: not a single album processed

    cache.resume_warmers()
    deadline = time.time() + 3.0
    while len(resolved) < 5 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    worker.wait(2000)
    assert sorted(resolved) == [0, 1, 2, 3, 4]


# -- AC4 --------------------------------------------------------------------
def test_warmers_wait_returns_when_stop_predicate_flips(tmp_path):
    cache = ArtworkCache(str(tmp_path / "artcache.db"))
    cache.pause_warmers()  # never resumed

    stop = {"v": False}
    threading.Timer(0.2, lambda: stop.__setitem__("v", True)).start()

    t0 = time.perf_counter()
    cache.warmers_wait_if_paused(lambda: stop["v"], poll=0.05)
    elapsed = time.perf_counter() - t0

    assert 0.1 < elapsed < 1.5  # returned via stop(), did not hang on the paused event
