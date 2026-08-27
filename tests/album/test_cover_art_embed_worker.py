"""Regression: adding/clearing album art must not run on the Qt UI thread.

Bug: "adding album art can lead to a thread lock while it processes."
AlbumCoverArtMixin._pick_cover() used to rewrite the embedded picture into
every track file (mutagen rewrites the whole file when the new image won't
fit existing padding) and then PIL-decode/resize/re-encode a cache
thumbnail -- all synchronously on the calling (UI) thread. For a
multi-track album or a large source image the event loop stalls for
seconds.

The fix moves that work onto CoverEmbedWorker (a background QThread) and
has the album view's ArtCacheWorker back off (ArtworkCache.pause_warmers)
while it runs so the two don't contend on the cache's single connection.

Each test maps 1:1 to an acceptance criterion:

  AC1  CoverEmbedWorker embeds every track off the calling thread and
       start() returns without waiting for the writes.
  AC2  A write failure is reported back in the `completed` payload and
       non-embeddable tracks are skipped.
  AC3  ArtCacheWorker processes no album while warmers are paused, and
       resumes once resume_warmers() is called.
  AC4  ArtworkCache.warmers_wait_if_paused() returns promptly when the
       caller's stop predicate flips, even while still paused (no hang,
       so a cancel during pause can't wedge the worker).
"""

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from src.album.album_art_worker import ArtCacheWorker, CoverEmbedWorker
from src.image.artwork_cache import ArtworkCache

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
