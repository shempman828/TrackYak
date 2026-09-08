"""
Lossless → MP3 transcoding for device sync.

`transcode_to_mp3()` is a thin ffmpeg wrapper (same subprocess discipline as
src/player/player_reader.py). `TranscodeCache` keeps the encoded MP3s on
disk keyed by source identity + bitrate, so a re-sync of the same library
is a stat, not a re-encode.

ffmpeg is an external binary, gated at runtime via `ffmpeg_available()` the
same way the MTP backends are — it is not a hard dependency.
"""

import contextlib
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from src.foundation.asset_paths import CACHE_DIR
from src.foundation.logger_config import logger

# Source extensions we transcode. Everything else (incl. .m4a/.m4b, which
# may be ALAC but are far more often AAC) is treated as already-lossy and
# copied verbatim.
LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif"}

DEFAULT_BITRATE = "320k"

# ffmpeg on a long lossless file still finishes in well under a minute
# (libmp3lame runs many times faster than realtime); 300s is pure headroom.
_TRANSCODE_TIMEOUT = 300

# `enforce_limit` sweeps orphaned "<dest>.<uuid>.part" temps (a hard kill mid-
# encode leaves them behind), but only ones older than this — a fresher .part
# may be a live encode from a second app instance.
_PART_SWEEP_AGE = 3600


class TranscodeError(RuntimeError):
    """ffmpeg failed to produce an MP3 for a given source."""


def is_lossless_path(path: str) -> bool:
    """True if `path`'s extension is one we transcode."""
    return Path(path).suffix.lower() in LOSSLESS_EXTENSIONS


@lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    """True if an `ffmpeg` binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def transcode_to_mp3(
    src: str, dest: Path, bitrate: str = DEFAULT_BITRATE, timeout: int = _TRANSCODE_TIMEOUT
) -> None:
    """
    Encode `src` to a CBR MP3 at `dest` (`dest` is replaced atomically).

    Tags and one embedded cover-art picture are carried over. Raises
    `TranscodeError` on any ffmpeg failure, leaving neither `dest` nor the
    temp file behind.
    """
    dest = Path(dest)
    # Unique temp name: parallel syncs can transcode two tracks that resolve
    # to the same cache dest concurrently — a shared "<name>.part" would have
    # them clobbering each other's in-progress encode. Each writes its own
    # temp and then atomically replaces dest (last writer wins, same bytes).
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        src,
        "-map",
        "0:a:0",
        "-map",
        "0:v:0?",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-c:v",
        "copy",
        "-disposition:v:0",
        "attached_pic",
        "-map_metadata",
        "0",
        "-id3v2_version",
        "3",
        "-write_id3v1",
        "1",
        # tmp has a .part extension ffmpeg can't map to a muxer — name it.
        "-f",
        "mp3",
        str(tmp),
    ]
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        tmp.unlink(missing_ok=True)
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else str(e)
        raise TranscodeError(detail[-500:]) from e
    tmp.replace(dest)


class TranscodeCache:
    """On-disk cache of transcoded MP3s, keyed by source identity + bitrate."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR / "transcode"

    def path_for(self, src: str, bitrate: str = DEFAULT_BITRATE) -> Path:
        """Deterministic cache path for `src` at `bitrate` (may not exist)."""
        real = Path(src).resolve()
        try:
            st = real.stat()
        except OSError as e:
            # Unmounted drive / since-deleted file. Raise the type sync loops
            # already catch, so one bad row is a skipped track, not an aborted
            # run.
            raise TranscodeError(f"source unavailable: {src} ({e})") from e
        key = f"{real}|{st.st_mtime_ns}|{st.st_size}|{bitrate}"
        digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()
        return self.cache_dir / f"{digest}.mp3"

    def get_or_create(self, src: str, bitrate: str = DEFAULT_BITRATE) -> Path:
        """
        Return the cached MP3 for `src`, transcoding it first on a cache
        miss. Raises `TranscodeError` if the encode fails.
        """
        dest = self.path_for(src, bitrate)
        if dest.exists() and dest.stat().st_size > 0:
            # Bump mtime so `enforce_limit` treats a re-synced entry as recently
            # used and ages out the stale ones instead. A read-only cache dir
            # just costs us eviction accuracy, never the sync.
            with contextlib.suppress(OSError):
                os.utime(dest, None)
            return dest
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        transcode_to_mp3(src, dest, bitrate)
        return dest

    def size_bytes(self) -> int:
        """
        Total size of the finished MP3s in the cache dir.

        `.part` temps left by a hard kill mid-encode are ignored — they're
        transient and shouldn't inflate the figure shown on the Clear button
        (`enforce_limit` sweeps them).
        """
        if not self.cache_dir.exists():
            return 0
        total = 0
        for entry in self.cache_dir.iterdir():
            if entry.suffix != ".mp3" or not entry.is_file():
                continue
            try:
                total += entry.stat().st_size
            except OSError:
                continue
        return total

    def enforce_limit(self, max_bytes: int) -> dict:
        """
        Sweep stale `.part` temps, then evict least-recently-used MP3s until the
        cache is back under `max_bytes`.

        "Least recently used" is oldest file mtime — `get_or_create` bumps mtime
        on every cache hit, so an entry that keeps getting re-synced stays warm
        while a stale post-re-tag entry ages out.

        `max_bytes <= 0` disables eviction (unlimited); the `.part` sweep still
        runs. Returns `{"evicted", "freed_bytes", "swept_parts"}`.
        """
        result = {"evicted": 0, "freed_bytes": 0, "swept_parts": 0}
        if not self.cache_dir.exists():
            return result

        now = time.time()
        entries: list[tuple[float, int, Path]] = []
        for entry in self.cache_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            if entry.suffix == ".part":
                if now - st.st_mtime > _PART_SWEEP_AGE:
                    try:
                        entry.unlink()
                        result["swept_parts"] += 1
                    except OSError as e:
                        logger.warning(f"Could not sweep stale temp {entry}: {e}")
                continue
            if entry.suffix == ".mp3":
                entries.append((st.st_mtime, st.st_size, entry))

        if max_bytes <= 0:
            return result

        total = sum(size for _, size, _ in entries)
        if total <= max_bytes:
            return result

        entries.sort(key=lambda t: t[0])  # oldest (least recently used) first
        for _mtime, size, path in entries:
            if total <= max_bytes:
                break
            try:
                path.unlink()
            except OSError as e:
                logger.warning(f"Could not evict cache file {path}: {e}")
                continue
            total -= size
            result["evicted"] += 1
            result["freed_bytes"] += size
        return result

    def clear(self) -> int:
        """Delete every file in the cache dir. Returns the count removed."""
        if not self.cache_dir.exists():
            return 0
        removed = 0
        for entry in self.cache_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError as e:
                logger.warning(f"Could not remove cache file {entry}: {e}")
        return removed
