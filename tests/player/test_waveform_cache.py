"""Coverage for src/player/waveform_cache.py — see docs/specs/waveform-scrubber.md.

AC1  path_for identity (dir, .v1.npy name, deterministic, mtime/size sensitive,
     missing source → WaveformError, not a bare OSError)
AC2  compute_peaks shape / dtype / range / col0<=col1 / loud > silent
AC3  compute_peaks still works via the streaming path when the header frame
     count is 0
AC4  generate() is atomic on write failure; load() hits without recomputing and
     bumps mtime
AC5  enforce_limit() LRU eviction, 0 = no-op, stale .tmp sweep
"""

import os
import time

import numpy as np
import pytest
import soundfile as sf

from src.player.waveform_cache import N_BUCKETS, WaveformCache, WaveformError, compute_peaks


def _make_wav(path, sr=8000, seconds=2.0):
    """Stereo float WAV: silent first half, full-scale 440 Hz tone second half."""
    n = int(sr * seconds)
    half = n // 2
    t = np.arange(n) / sr
    tone = 0.9 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    tone[:half] = 0.0
    sf.write(str(path), np.stack([tone, tone], axis=1), sr, subtype="FLOAT")
    return path


# AC1 -----------------------------------------------------------------------


def test_path_for_is_deterministic_and_under_cache_dir(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    wav = _make_wav(tmp_path / "a.wav")

    p1 = cache.path_for(wav)
    assert p1.parent == tmp_path / "wf"
    assert p1.name.endswith(".v1.npy")
    assert p1 == cache.path_for(wav)


def test_path_for_changes_with_mtime_and_size(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    wav = _make_wav(tmp_path / "a.wav", seconds=1.0)
    p1 = cache.path_for(wav)

    future = time.time() + 500
    os.utime(wav, (future, future))
    p2 = cache.path_for(wav)
    assert p2 != p1

    _make_wav(wav, seconds=3.0)  # different size
    assert cache.path_for(wav) not in (p1, p2)


def test_path_for_missing_source_raises_waveform_error_not_oserror(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    with pytest.raises(WaveformError) as exc:
        cache.path_for(tmp_path / "nope.wav")
    assert not isinstance(exc.value, OSError)


# AC2 -----------------------------------------------------------------------


def test_compute_peaks_shape_range_monotonic_and_loud_vs_silent(tmp_path):
    wav = _make_wav(tmp_path / "a.wav", sr=8000, seconds=2.0)

    peaks = compute_peaks(wav, n_buckets=200)

    assert peaks.dtype == np.int8
    assert peaks.shape == (200, 2)
    assert peaks.min() >= -127 and peaks.max() <= 127
    assert np.all(peaks[:, 0] <= peaks[:, 1])

    silent = np.abs(peaks[:100]).mean()
    loud = np.abs(peaks[100:]).mean()
    assert loud > silent + 20


# AC3 -----------------------------------------------------------------------


def test_compute_peaks_uses_streaming_path_when_header_count_zero(tmp_path, monkeypatch):
    wav = _make_wav(tmp_path / "a.wav", sr=8000, seconds=2.0)
    monkeypatch.setattr("src.player.waveform_cache._frame_count", lambda _r: 0)

    peaks = compute_peaks(wav, n_buckets=200)

    assert peaks.shape == (200, 2)
    assert np.all(peaks[:, 0] <= peaks[:, 1])
    assert np.abs(peaks[100:]).mean() > np.abs(peaks[:100]).mean() + 20


# AC4 -----------------------------------------------------------------------


def test_generate_leaves_nothing_behind_on_write_failure(tmp_path, monkeypatch):
    cache = WaveformCache(tmp_path / "wf")
    cache._swept = True
    wav = _make_wav(tmp_path / "a.wav")

    def boom(fh, _arr):
        fh.write(b"half a file")
        raise OSError("disk full")

    monkeypatch.setattr(np, "save", boom)
    with pytest.raises(OSError):
        cache.generate(wav)

    dest = cache.path_for(wav)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".tmp").exists()


def test_get_hits_cache_without_recompute_and_bumps_mtime(tmp_path, monkeypatch):
    cache = WaveformCache(tmp_path / "wf")
    cache._swept = True
    wav = _make_wav(tmp_path / "a.wav")

    cache.generate(wav)
    dest = cache.path_for(wav)
    stale = dest.stat().st_mtime - 900
    os.utime(dest, (stale, stale))

    calls = []
    monkeypatch.setattr("src.player.waveform_cache.compute_peaks", lambda *a, **k: calls.append(1))

    arr = cache.get(wav)

    assert calls == []
    assert arr.shape == (N_BUCKETS, 2)
    assert dest.stat().st_mtime > stale


# AC5 -----------------------------------------------------------------------


def _seed(cache_dir, name, size, mtime):
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / name
    f.write_bytes(b"x" * size)
    os.utime(f, (mtime, mtime))
    return f


def test_enforce_limit_evicts_oldest_mtime_first(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    _seed(cache.cache_dir, "old.v1.npy", 1000, 1_000)
    _seed(cache.cache_dir, "mid.v1.npy", 1000, 1_100)
    new = _seed(cache.cache_dir, "new.v1.npy", 1000, 1_200)

    result = cache.enforce_limit(1500)

    assert not (cache.cache_dir / "old.v1.npy").exists()
    assert not (cache.cache_dir / "mid.v1.npy").exists()
    assert new.exists()
    assert result["evicted"] == 2
    assert result["freed_bytes"] == 2000


def test_enforce_limit_zero_is_a_noop(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    a = _seed(cache.cache_dir, "a.v1.npy", 5000, 1_000)
    b = _seed(cache.cache_dir, "b.v1.npy", 5000, 1_100)

    result = cache.enforce_limit(0)

    assert a.exists() and b.exists()
    assert result["evicted"] == 0
    assert result["freed_bytes"] == 0


def test_enforce_limit_sweeps_only_stale_tmp_files(tmp_path):
    cache = WaveformCache(tmp_path / "wf")
    stale = _seed(cache.cache_dir, "aaa.v1.npy.tmp", 10, 1)
    fresh = cache.cache_dir / "bbb.v1.npy.tmp"
    fresh.write_bytes(b"x")

    result = cache.enforce_limit(0)

    assert not stale.exists()
    assert fresh.exists()
    assert result["swept_tmp"] == 1
