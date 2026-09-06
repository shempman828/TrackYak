"""Regression coverage for artwork_cache.all_album_tracks
(src/image/artwork_cache.py): the album-art embed pass
(AlbumCoverArtMixin._start_cover_embed) and _pick_representative_track must
see every track that belongs to an album, including any reachable only
through Album.discs -> Disc.tracks -- a track detached from its album but
left sitting on one of its discs. If such a track is skipped, "Remove album
art" never strips its file and the stale cover later resurfaces as art on
whatever album the track is added to next.

Also covers the read-only-window resilience (ArtworkCache._note_write_failure
/ is_degraded): a cache-db write failure must not raise out of get_pixmap /
get_dimensions, must be logged once (not per lookup), must stop lookups from
re-running the expensive extract+decode, and must self-heal on reconnect.
"""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from src.image import artwork_cache as ac_mod
from src.image.artwork_cache import ArtworkCache, all_album_tracks


def _track(tid):
    return SimpleNamespace(track_id=tid)


def test_unions_direct_and_disc_only_tracks():
    t1, t2, t3 = _track(1), _track(2), _track(3)
    disc = SimpleNamespace(tracks=[t2, t3])
    album = SimpleNamespace(tracks=[t1], discs=[disc])

    ids = sorted(t.track_id for t in all_album_tracks(album))
    assert ids == [1, 2, 3]


def test_deduplicates_tracks_reachable_both_ways():
    shared = _track(1)
    disc = SimpleNamespace(tracks=[shared, _track(2)])
    album = SimpleNamespace(tracks=[shared], discs=[disc])

    result = all_album_tracks(album)
    assert sorted(t.track_id for t in result) == [1, 2]


def test_direct_instance_wins_on_dedup():
    direct = _track(1)
    disc = SimpleNamespace(tracks=[_track(1)])
    album = SimpleNamespace(tracks=[direct], discs=[disc])

    result = all_album_tracks(album)
    assert result == [direct]
    assert result[0] is direct


def test_handles_missing_or_empty_relationships():
    assert all_album_tracks(SimpleNamespace(tracks=None, discs=None)) == []
    assert all_album_tracks(SimpleNamespace()) == []
    album = SimpleNamespace(tracks=[_track(5)], discs=[])
    assert [t.track_id for t in all_album_tracks(album)] == [5]


def test_skips_tracks_without_an_id():
    album = SimpleNamespace(tracks=[_track(7), SimpleNamespace()], discs=[])
    assert [t.track_id for t in all_album_tracks(album)] == [7]


# --------------------------------------------------------------------------- #
#  Read-only-window resilience                                                 #
# --------------------------------------------------------------------------- #

_WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "CREATE", "REPLACE"}


def _album_with_track(tmp_path):
    f = tmp_path / "01 - track.flac"
    f.write_bytes(b"not really a flac")
    track = SimpleNamespace(track_id=1, track_file_path=str(f), disc_id=None, track_number=1)
    return SimpleNamespace(album_id=42, tracks=[track], discs=[])


class _ReadOnlyConn:
    """Proxy around a real sqlite3 connection that fails write statements
    with the actual read-only error, but lets SELECTs through (they work
    fine against a read-only sqlite db). Used because sqlite3.Connection
    attributes can't be monkeypatched directly."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args, **kwargs):
        if sql.strip().split(None, 1)[0].upper() in _WRITE_VERBS:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = ArtworkCache(db_path=str(tmp_path / "artwork_cache.db"))
    # _refresh() would otherwise read+decode a real audio file; pretend the
    # track simply carries no embedded art so it goes straight to _upsert.
    calls = {"n": 0}

    def _extract(*_a, **_k):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(c._extractor, "extract_artwork_by_role", _extract)
    c.extract_calls = calls
    yield c
    c.close()


def test_write_failure_does_not_raise_and_logs_once(cache, tmp_path, monkeypatch):
    album = _album_with_track(tmp_path)
    monkeypatch.setattr(cache, "_conn", _ReadOnlyConn(cache._conn))
    monkeypatch.setattr(cache, "_reconnect_locked", lambda: False)

    warnings = []
    monkeypatch.setattr(ac_mod.logger, "warning", lambda m, *a: warnings.append(m % a if a else m))

    # Three lookups that each miss the cache -> each tries to write.
    assert cache.get_dimensions(album, "front") is None
    assert cache.get_dimensions(album, "front") is None
    assert cache.get_dimensions(album, "rear") is None

    assert cache.is_degraded()
    assert len(warnings) == 1  # logged on entry to the window, not per failed write


def test_degraded_window_short_circuits_refresh(cache, tmp_path, monkeypatch):
    album = _album_with_track(tmp_path)
    cache._degraded_until = time.monotonic() + 60

    assert cache.get_dimensions(album, "front") is None
    # _refresh (and its extract_artwork_by_role) must not have been touched.
    assert cache.extract_calls["n"] == 0


def test_degraded_window_serves_stale_cached_row(cache, tmp_path, monkeypatch):
    album = _album_with_track(tmp_path)
    track = album.tracks[0]

    # Warm a real row, then make the source file look changed so the row is
    # now stale (mtime mismatch) and would normally trigger a _refresh.
    cache.store(album, "front", _png_1x1())
    assert cache.get_dimensions(album, "front") == (1, 1)
    import os

    os.utime(track.track_file_path, (0, 0))

    cache._degraded_until = time.monotonic() + 60
    # Stale but served as-is instead of re-extracting during the window.
    assert cache.get_dimensions(album, "front") == (1, 1)
    assert cache.extract_calls["n"] == 0


def test_reconnect_retry_recovers_without_degrading(cache, tmp_path, monkeypatch):
    album = _album_with_track(tmp_path)
    # Live connection can't write, but a reconnect yields a writable one
    # (real _reconnect_locked opens a fresh sqlite3 connection to the same
    # file, which is not read-only in the test).
    monkeypatch.setattr(cache, "_conn", _ReadOnlyConn(cache._conn))

    warnings = []
    monkeypatch.setattr(ac_mod.logger, "warning", lambda m, *a: warnings.append(m))

    cache.get_dimensions(album, "front")

    assert not cache.is_degraded()
    assert warnings == []
    # Row actually landed via the reconnected handle.
    assert cache._select(42, "front") is not None


def test_warmer_stops_when_cache_is_read_only(tmp_path):
    from src.album.album_art_worker import ArtCacheWorker

    seen = []

    class _Cache:
        def is_degraded(self):
            return True

        def warmers_wait_if_paused(self, _stop):
            seen.append("waited")

        def get_dimensions(self, *_a):
            seen.append("read")

    worker = ArtCacheWorker([SimpleNamespace(album_id=1)], _Cache(), "front")
    worker.run()
    assert seen == []  # bailed before doing any work on the unwritable db


def _png_1x1():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()
