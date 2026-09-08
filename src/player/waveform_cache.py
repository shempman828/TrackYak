"""
waveform_cache.py — per-track downsampled amplitude envelopes for the
Player Dock's waveform seek bar.

One tiny ``.npy`` per track identity under ``cache/waveforms/``, keyed the
same way as the sync transcode cache (resolved path | mtime | size). The
array is ``int8``, shape ``(N_BUCKETS, 2)``: column 0 = bucket minimum
sample, column 1 = bucket maximum, quantised to ``[-127, 127]``.

Decoding reuses :func:`player_reader._open_soundfile`, so every container the
player can play — including the ``.m4a``/``.aac``/``.opus`` ffmpeg-to-WAV
fallback — is covered.
"""

import contextlib
import hashlib
import math
import os
from pathlib import Path
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal
import soundfile as _sf

from src.foundation.asset_paths import WAVEFORMCACHE_DIR
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.player.player_reader import _open_soundfile

# Bucket count is fixed regardless of the seek bar's pixel width — the widget
# aggregates buckets down to its width at paint time. 2000 int8 pairs is
# ~4.1 KB on disk and finer than any realistic docked-bar width.
N_BUCKETS = 2000
# On-disk format tag. Bump to invalidate every cached file at once if the
# array layout / quantisation changes.
FORMAT_VERSION = "v1"
# Cap on frames pulled from libsndfile per read() call, so a huge
# frames-per-bucket (very long file) can't balloon memory.
_SUBCHUNK_FRAMES = 1 << 20
# Leftover "<name>.tmp" older than this is swept by enforce_limit; a fresher
# one may be a live write from a second app instance.
_TMP_SWEEP_AGE = 3600


class WaveformError(RuntimeError):
    """Source file unavailable / unreadable for peak generation.

    A ``RuntimeError`` (not ``OSError``) so callers can tell a real
    generation failure apart from an incidental filesystem error.
    """


# ---------------------------------------------------------------------------
# Peak computation (no Qt, unit-testable on its own)
# ---------------------------------------------------------------------------


def _frame_count(reader) -> int:
    """Frames the container header claims. Seam for tests to force the
    streaming path."""
    try:
        return len(reader)
    except (TypeError, RuntimeError):
        return 0


def _quantise(mins: np.ndarray, maxs: np.ndarray) -> np.ndarray:
    """Pack float32 min/max envelopes into an ``(N, 2)`` int8 array."""
    out = np.empty((len(mins), 2), dtype=np.int8)
    out[:, 0] = np.clip(np.round(mins * 127.0), -127, 127).astype(np.int8)
    out[:, 1] = np.clip(np.round(maxs * 127.0), -127, 127).astype(np.int8)
    # Rounding can't cross min past max, but a defensive swap costs nothing
    # and keeps the col0 <= col1 invariant absolute.
    crossed = out[:, 0] > out[:, 1]
    out[crossed, 0], out[crossed, 1] = out[crossed, 1], out[crossed, 0].copy()
    return out


def _reduce_known_length(reader, n_frames: int, n_buckets: int) -> np.ndarray:
    """Sequential min/max reduction when the header frame count is trusted."""
    mins = np.zeros(n_buckets, dtype=np.float32)
    maxs = np.zeros(n_buckets, dtype=np.float32)
    frames_per_bucket = max(1, math.ceil(n_frames / n_buckets))
    remaining = n_frames
    for b in range(n_buckets):
        if remaining <= 0:
            break
        want = min(frames_per_bucket, remaining)
        remaining -= want
        lo = math.inf
        hi = -math.inf
        got = 0
        while got < want:
            block = reader.read(min(_SUBCHUNK_FRAMES, want - got), dtype="float32", always_2d=True)
            if not len(block):
                break
            lo = min(lo, float(block.min()))
            hi = max(hi, float(block.max()))
            got += len(block)
        if got:
            mins[b] = 0.0 if lo == math.inf else lo
            maxs[b] = 0.0 if hi == -math.inf else hi
    return _quantise(mins, maxs)


def _reduce_streaming(reader, n_buckets: int) -> np.ndarray:
    """Fallback for files whose header reports 0 / an unreliable frame count
    (streaming FLAC, some VBR MP3): decode fully, then bucket by index."""
    blocks = [
        block
        for block in reader.blocks(blocksize=_SUBCHUNK_FRAMES, dtype="float32", always_2d=True)
        if len(block)
    ]
    total = sum(len(b) for b in blocks)
    if not total:
        zero = np.zeros(n_buckets, dtype=np.float32)
        return _quantise(zero, zero)
    data = np.concatenate(blocks, axis=0)
    frame_min = data.min(axis=1)
    frame_max = data.max(axis=1)
    edges = np.linspace(0, total, n_buckets + 1, dtype=np.int64)
    mins = np.zeros(n_buckets, dtype=np.float32)
    maxs = np.zeros(n_buckets, dtype=np.float32)
    for b in range(n_buckets):
        s = int(edges[b])
        e = max(int(edges[b + 1]), s + 1)
        seg = frame_min[s:e]
        if len(seg):
            mins[b] = seg.min()
            maxs[b] = frame_max[s:e].max()
    return _quantise(mins, maxs)


def compute_peaks(path, n_buckets: int = N_BUCKETS) -> np.ndarray:
    """Decode ``path`` and return an ``(n_buckets, 2)`` int8 min/max envelope."""
    reader = _open_soundfile(_sf, Path(path))
    try:
        n_frames = _frame_count(reader)
        if n_frames and n_frames > 0:
            return _reduce_known_length(reader, n_frames, n_buckets)
        return _reduce_streaming(reader, n_buckets)
    finally:
        with contextlib.suppress(OSError, _sf.LibsndfileError):
            reader.close()


# ---------------------------------------------------------------------------
# On-disk cache
# ---------------------------------------------------------------------------


class WaveformCache:
    """Cache-first access to per-track peak envelopes under ``cache/waveforms/``."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else WAVEFORMCACHE_DIR
        self._swept = False
        self._sweep_lock = threading.Lock()

    def path_for(self, src) -> Path:
        """Deterministic cache path for ``src`` (may not exist).

        Raises :class:`WaveformError` if the source can't be stat'd."""
        real = Path(src).resolve()
        try:
            st = real.stat()
        except OSError as e:
            raise WaveformError(f"source unavailable: {src} ({e})") from e
        key = f"{real}|{st.st_mtime_ns}|{st.st_size}"
        digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()
        return self.cache_dir / f"{digest}.{FORMAT_VERSION}.npy"

    def load(self, src) -> np.ndarray | None:
        """Return the cached envelope for ``src`` or ``None`` on a miss.

        A cache hit bumps the file's mtime so a frequently played track
        survives LRU eviction."""
        dest = self.path_for(src)
        try:
            if not (dest.exists() and dest.stat().st_size > 0):
                return None
            arr = np.load(dest)
        except (OSError, ValueError) as e:
            logger.warning(f"WaveformCache: dropping unreadable {dest.name}: {e}")
            with contextlib.suppress(OSError):
                dest.unlink()
            return None
        with contextlib.suppress(OSError):
            os.utime(dest, None)
        return arr

    def generate(self, src) -> np.ndarray:
        """Compute the envelope for ``src`` and write it atomically."""
        dest = self.path_for(src)
        peaks = compute_peaks(src)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            with tmp.open("wb") as fh:
                np.save(fh, peaks)
            tmp.replace(dest)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        return peaks

    def get(self, src) -> np.ndarray:
        """Cache-first: the stored envelope, or a freshly generated + stored one."""
        self._ensure_swept()
        cached = self.load(src)
        if cached is not None:
            return cached
        return self.generate(src)

    # -- housekeeping -------------------------------------------------------

    def _ensure_swept(self) -> None:
        """Run one LRU sweep per process, lazily, on first cache use."""
        with self._sweep_lock:
            if self._swept:
                return
            self._swept = True  # set first: a failed sweep must not retry every call
        max_mb = app_config.get_waveform_cache_max_mb()
        if not max_mb or max_mb <= 0:
            return
        try:
            result = self.enforce_limit(max_mb * 1024 * 1024)
        except OSError as e:  # pragma: no cover - defensive
            logger.warning(f"WaveformCache: enforce_limit failed: {e}")
            return
        if result["evicted"] or result["swept_tmp"]:
            logger.info(
                f"WaveformCache: swept {result['swept_tmp']} tmp, evicted "
                f"{result['evicted']} file(s) ({result['freed_bytes']} bytes)"
            )

    def enforce_limit(self, max_bytes: int) -> dict:
        """Sweep stale ``.tmp`` files, then LRU-evict ``.npy`` files until the
        total is back under ``max_bytes``. ``max_bytes <= 0`` skips eviction
        (the tmp sweep still runs)."""
        swept_tmp = 0
        evicted = 0
        freed = 0
        if not self.cache_dir.exists():
            return {"evicted": 0, "freed_bytes": 0, "swept_tmp": 0}

        now = time.time()
        entries: list[tuple[Path, float, int]] = []
        for entry in self.cache_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.name.endswith(".tmp"):
                with contextlib.suppress(OSError):
                    if now - entry.stat().st_mtime > _TMP_SWEEP_AGE:
                        entry.unlink()
                        swept_tmp += 1
                continue
            if entry.suffix == ".npy":
                with contextlib.suppress(OSError):
                    st = entry.stat()
                    entries.append((entry, st.st_mtime, st.st_size))

        if max_bytes <= 0:
            return {"evicted": 0, "freed_bytes": 0, "swept_tmp": swept_tmp}

        total = sum(size for _, _, size in entries)
        if total <= max_bytes:
            return {"evicted": 0, "freed_bytes": 0, "swept_tmp": swept_tmp}

        entries.sort(key=lambda t: t[1])  # oldest mtime first
        for entry, _mtime, size in entries:
            if total <= max_bytes:
                break
            with contextlib.suppress(OSError):
                entry.unlink()
                evicted += 1
                freed += size
                total -= size
        return {"evicted": evicted, "freed_bytes": freed, "swept_tmp": swept_tmp}


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class WaveformWorkerSignals(QObject):
    # (generation, src, np.ndarray) / (generation, src, error message). The
    # generation rides along so the receiver can be a plain bound slot (Qt
    # auto-disconnects it when the widget dies) instead of a lambda closure
    # over a possibly-deleted widget.
    ready = Signal(int, Path, object)
    failed = Signal(int, Path, str)


class WaveformWorker(QRunnable):
    """One-shot ``QRunnable``: resolve ``src``'s envelope (cache hit or fresh
    decode) off the UI thread, then emit it. ``generation`` is opaque here —
    the caller uses it to drop a result whose track has since changed."""

    def __init__(self, cache: WaveformCache, src, generation: int):
        super().__init__()
        self.cache = cache
        self.src = Path(src)
        self.generation = generation
        self.signals = WaveformWorkerSignals()

    def run(self) -> None:
        try:
            peaks = self.cache.get(self.src)
        except Exception as e:
            logger.warning(f"WaveformWorker: {self.src.name}: {e}")
            self.signals.failed.emit(self.generation, self.src, str(e))
            return
        self.signals.ready.emit(self.generation, self.src, peaks)


# Shared singleton — the dock and any future consumer reference one instance
# so the once-per-session sweep really is once.
waveform_cache = WaveformCache()
