"""
Transcode cache size management — idea 57, bugs 515-517.

None of this shells out to ffmpeg: the cache dir is just files on disk, so the
tests fabricate `.mp3` / `.part` entries directly and drive `TranscodeCache`
against them.
"""

import os
import time

import pytest

from src.sync import transcode
from src.sync.transcode import TranscodeCache, TranscodeError


def _entry(cache_dir, name, *, size=1024, age_s=0.0):
    """Write `name` into `cache_dir` with `size` bytes, mtime `age_s` in the past."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / name
    p.write_bytes(b"\0" * size)
    if age_s:
        past = time.time() - age_s
        os.utime(p, (past, past))
    return p


# --- path_for on a missing source -------------------------------------------- AC1


def test_path_for_missing_source_raises_transcode_error(tmp_path):
    cache = TranscodeCache(cache_dir=tmp_path / "cache")
    with pytest.raises(TranscodeError):
        cache.path_for(str(tmp_path / "gone.flac"))


def test_get_or_create_missing_source_raises_transcode_error(tmp_path):
    cache = TranscodeCache(cache_dir=tmp_path / "cache")
    with pytest.raises(TranscodeError):
        cache.get_or_create(str(tmp_path / "gone.flac"))


# --- mtime bump on cache hit ------------------------------------------------- AC3


def test_cache_hit_bumps_mtime(tmp_path, monkeypatch):
    src = tmp_path / "src.flac"
    src.write_bytes(b"not really audio, never transcoded")
    cache = TranscodeCache(cache_dir=tmp_path / "cache")

    dest = cache.path_for(str(src))
    _entry(dest.parent, dest.name, age_s=10_000)
    stale_mtime = dest.stat().st_mtime

    def _fail(*a, **kw):
        raise AssertionError("cache hit should not transcode")

    monkeypatch.setattr(transcode, "transcode_to_mp3", _fail)

    got = cache.get_or_create(str(src))

    assert got == dest
    assert dest.stat().st_mtime > stale_mtime


# --- enforce_limit: under / over / unlimited ------------------------------ AC4-6


def test_enforce_limit_under_cap_is_noop(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "a.mp3", size=1000)
    _entry(cache_dir, "b.mp3", size=1000)
    cache = TranscodeCache(cache_dir=cache_dir)

    out = cache.enforce_limit(10_000)

    assert out["evicted"] == 0
    assert len(list(cache_dir.glob("*.mp3"))) == 2


def test_enforce_limit_evicts_least_recently_used_first(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "old.mp3", size=1000, age_s=3000)
    _entry(cache_dir, "mid.mp3", size=1000, age_s=2000)
    _entry(cache_dir, "new.mp3", size=1000, age_s=1000)
    cache = TranscodeCache(cache_dir=cache_dir)

    # Cap fits one entry; the two oldest must go.
    out = cache.enforce_limit(1500)

    assert out["evicted"] == 2
    assert out["freed_bytes"] == 2000
    survivors = {p.name for p in cache_dir.glob("*.mp3")}
    assert survivors == {"new.mp3"}


def test_enforce_limit_zero_means_unlimited(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "a.mp3", size=5000)
    _entry(cache_dir, "b.mp3", size=5000)
    cache = TranscodeCache(cache_dir=cache_dir)

    out = cache.enforce_limit(0)

    assert out["evicted"] == 0
    assert len(list(cache_dir.glob("*.mp3"))) == 2


# --- .part sweep ----------------------------------------------------------- AC7


def test_enforce_limit_sweeps_stale_parts_only(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "out.mp3.deadbeef.part", age_s=transcode._PART_SWEEP_AGE + 60)
    _entry(cache_dir, "out.mp3.feedface.part", age_s=10)  # fresh — live encode
    cache = TranscodeCache(cache_dir=cache_dir)

    out = cache.enforce_limit(0)

    assert out["swept_parts"] == 1
    remaining = {p.name for p in cache_dir.glob("*.part")}
    assert remaining == {"out.mp3.feedface.part"}


# --- size_bytes ignores .part; clear still removes it --------------------- AC8


def test_size_bytes_ignores_part_files(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "a.mp3", size=2000)
    _entry(cache_dir, "half.mp3.abc123.part", size=9999)
    cache = TranscodeCache(cache_dir=cache_dir)

    assert cache.size_bytes() == 2000


def test_clear_still_removes_part_files(tmp_path):
    cache_dir = tmp_path / "cache"
    _entry(cache_dir, "a.mp3", size=2000)
    _entry(cache_dir, "half.mp3.abc123.part", size=9999)
    cache = TranscodeCache(cache_dir=cache_dir)

    removed = cache.clear()

    assert removed == 2
    assert not list(cache_dir.iterdir())
