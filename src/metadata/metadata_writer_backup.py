"""Shared backup/restore/verify helpers used by the MP3 and FLAC file
writers when writing tags or artwork.
"""

from collections.abc import Callable
import contextlib
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import time
from typing import Any

from PIL import Image

from src.foundation.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor

_TMP_PREFIX = ".tmp-"

# atomic_write temps that survive longer than this are assumed abandoned by a
# hard-killed writer, not in flight. Any real atomic_write finishes in well
# under a second; the margin only has to clear a concurrent writer in the same
# directory whose temp we must not reap out from under it.
_STALE_TMP_MIN_AGE_S = 60

# Directories this process has already swept. Orphaned temps are always left by
# an *earlier* process, so one sweep per directory per process catches them all.
_swept_dirs: set[str] = set()


def sweep_stale_temp_files(directory: str, *, min_age_seconds: int = _STALE_TMP_MIN_AGE_S) -> None:
    """Delete abandoned `.tmp-*` files left in `directory`.

    atomic_write creates its temp with mkstemp and only unlinks it on a
    handled exception. A SIGKILL / power loss between mkstemp and os.replace
    leaves the temp stranded next to the target with no cleanup path. This
    reaps those, but only ones older than `min_age_seconds` so a concurrent
    in-flight atomic_write in the same directory is never touched. Best
    effort - every failure is swallowed.
    """
    now = time.time()
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(_TMP_PREFIX):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if now - entry.stat().st_mtime < min_age_seconds:
                continue
            Path(entry.path).unlink()
            logger.debug(f"Swept stale atomic-write temp file: {entry.path}")
        except OSError:
            continue


def _copy_xattrs(src: str, dst: str) -> None:
    """Copy every extended attribute from src to dst (best effort).

    shutil.copymode drops these; on Linux the `user.*` namespace is where
    some players stash rating tags, so losing them on the swapped-in inode
    is silent metadata loss.
    """
    if not hasattr(os, "listxattr"):
        return
    try:
        names = os.listxattr(src, follow_symlinks=False)
    except OSError:
        return
    for name in names:
        try:
            os.setxattr(
                dst, name, os.getxattr(src, name, follow_symlinks=False), follow_symlinks=False
            )
        except OSError:
            continue


def _clone_file_metadata(src: str, dst: str) -> None:
    """Carry filesystem metadata from src onto the temp that will replace it.

    Restores permission bits, ownership (best effort - needs privilege when
    uid/gid differ; mkstemp already makes dst caller-owned, which covers the
    common case), and extended attributes. Timestamps are deliberately NOT
    copied: dst must keep its fresh mtime so mtime-keyed caches downstream
    (src/image/artwork_cache.py, src/sync/transcode.py) still see the file
    as changed. POSIX ACLs beyond the base mode are not copied - there is no
    stdlib API for them.
    """
    try:
        st = Path(src).stat()
    except OSError:
        return
    with contextlib.suppress(OSError):
        Path(dst).chmod(stat.S_IMODE(st.st_mode))
    if hasattr(os, "chown"):
        with contextlib.suppress(OSError):
            os.chown(dst, st.st_uid, st.st_gid)
    _copy_xattrs(src, dst)


def atomic_write(file_path: str, data: bytes) -> None:
    """Write `data` to file_path via a temp file + atomic rename.

    A straight open(file_path, "wb")/"r+b" rewrite mutates the file's
    existing inode in place. If another thread or process (e.g. the audio
    player streaming this exact track) already has the file open, its read
    position is based on the old byte layout - rewriting the tag/artwork
    shifts where the audio data starts, so the player's next read lands on
    the wrong bytes (FLAC decoder desync, corrupt MP3 frames, etc).
    Writing to a new temp file and renaming it over file_path swaps the
    directory entry to a new inode; any fd already open on the old one
    keeps reading its original, untouched bytes until it's closed and
    reopened.

    Permission bits, ownership, and extended attributes are cloned onto the
    replacement inode (see _clone_file_metadata); a hard kill mid-write can
    still strand the temp, so the first write to each directory in this
    process sweeps that directory's stale `.tmp-*` leftovers first.
    """
    directory = str(Path(file_path).parent)
    if directory not in _swept_dirs:
        _swept_dirs.add(directory)
        sweep_stale_temp_files(directory)
    suffix = Path(file_path).suffix
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=_TMP_PREFIX, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _clone_file_metadata(file_path, tmp_path)
        Path(tmp_path).replace(file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()
        raise


def backup_file(file_path: str) -> str:
    """Copy file_path to a sibling .bak file and return its path.

    Refuses (FileExistsError) to overwrite an existing <file>.bak. A
    leftover backup means an earlier tag/artwork write died after
    backup_file but before restore_backup/discard_backup ran - so that .bak
    is the only untouched copy of the original, and file_path itself may
    already be half-written. Blindly re-copying would replace the pristine
    backup with the modified file and destroy the last good copy. Callers
    catch this as "back up failed - abort the write", leaving the stale
    backup in place for manual recovery.
    """
    backup_path = file_path + ".bak"
    if Path(backup_path).exists():
        raise FileExistsError(
            f"Refusing to overwrite existing backup {backup_path}: a prior write "
            f"likely failed without restoring. Confirm {file_path} is intact, then "
            f"move or remove the backup."
        )
    shutil.copy2(file_path, backup_path)
    return backup_path


def restore_backup(file_path: str, backup_path: str) -> bool:
    """
    Best-effort restore of file_path from backup_path. Never raises - a
    failed restore (e.g. the same permission error that caused the
    original write to fail) must not crash the caller. On failure, the
    backup is deliberately left in place for manual recovery instead of
    being deleted.
    """
    try:
        shutil.copy2(backup_path, file_path)
        Path(backup_path).unlink()
        return True
    except OSError as e:
        logger.error(
            f"Failed to restore {file_path} from backup after a write "
            f"error: {e}. Backup preserved at {backup_path}"
        )
        return False


def discard_backup(backup_path: str) -> None:
    """Remove a backup after a successful write."""
    Path(backup_path).unlink(missing_ok=True)


def write_artwork_with_backup(
    file_path: str,
    role: str,
    image_bytes: Any,
    role_to_type: dict[str, int],
    mutate: Callable[[], bool],
    error_context: str,
) -> bool:
    """
    Shared control-flow skeleton for a format's write_artwork: validate
    `role`, back up the file, run `mutate` (which does the format-specific
    strip-existing-picture/append-new-picture/serialize and writes the
    file), then verify the result and restore the backup on any failure.

    `mutate` returns False if it couldn't find anything to write against
    (e.g. no parseable metadata blocks/tag) - treated the same as any other
    failure, but without a verification step since nothing was written.
    `error_context` is folded into the debug log line on an exception (e.g.
    "artwork" or "MP3 artwork") so failures are still distinguishable by format.
    """
    if role not in role_to_type:
        raise ValueError(f"Unknown artwork role: {role}")

    if not os.access(file_path, os.W_OK):
        logger.debug(f"Skipping artwork write - not writable: {file_path}")
        return False

    backup_path = None
    try:
        backup_path = backup_file(file_path)

        if not mutate():
            discard_backup(backup_path)
            return False

        if not verify_artwork_write(file_path, role, image_bytes):
            restore_backup(file_path, backup_path)
            logger.error(
                f"Artwork write verification failed for {file_path} "
                f"(role={role}); attempted to restore backup"
            )
            return False

        discard_backup(backup_path)
        return True

    except (OSError, struct.error, Image.DecompressionBombError) as e:
        logger.debug(f"Error writing {error_context} to {file_path}: {e}")
        if backup_path and Path(backup_path).exists():
            restore_backup(file_path, backup_path)
        return False


def verify_artwork_write(file_path: str, role: str, image_bytes: Any) -> bool:
    """Re-read the file and confirm the write did what it was meant to do."""
    ext = Path(file_path).suffix.lower()
    result = ArtworkExtractor().extract_artwork_by_role(file_path, ext)

    if image_bytes is None:
        return role not in result

    picture = result.get(role)
    if not picture:
        return False

    return hashlib.sha256(picture["data"]).digest() == hashlib.sha256(image_bytes).digest()
