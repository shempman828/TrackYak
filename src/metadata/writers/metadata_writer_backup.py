"""Shared atomic-write, backup/restore and verify helpers for the tag and artwork writers."""

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
    """Best-effort delete of `.tmp-*` files older than min_age_seconds that a killed atomic_write left behind."""
    # The age limit keeps a concurrent in-flight atomic_write's temp safe.
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
    """Best-effort copy of every extended attribute from src to dst (some players keep ratings in user.*)."""
    if not hasattr(os, "listxattr"):
        return
    try:
        names = os.listxattr(src, follow_symlinks=False)
    except OSError:
        return
    for name in names:
        try:
            os.setxattr(dst, name, os.getxattr(src, name, follow_symlinks=False), follow_symlinks=False)
        except OSError:
            continue


def _clone_file_metadata(src: str, dst: str) -> None:
    """Copy permission bits, ownership and xattrs from src to the temp that replaces it."""
    # Timestamps are not copied: mtime-keyed caches (artwork_cache, transcode) must see the change.
    # POSIX ACLs beyond the base mode are not copied (no stdlib API).
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
    """Write data to file_path through a temp file and an atomic rename onto a new inode."""
    # A new inode keeps the player's open fd on the old bytes, so a playing track does not desync.
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
    """Copy file_path to a sibling .bak file and return its path; raise FileExistsError if one exists."""
    # A leftover .bak is from a write that died mid-way and may be the only good copy, so never overwrite it.
    backup_path = file_path + ".bak"
    if Path(backup_path).exists():
        raise FileExistsError(f"Refusing to overwrite existing backup {backup_path}: a prior write likely failed without restoring. Confirm {file_path} is intact, then move or remove the backup.")
    shutil.copy2(file_path, backup_path)
    return backup_path


def restore_backup(file_path: str, backup_path: str) -> bool:
    """Best-effort restore of file_path from backup_path; never raises, and keeps the backup on failure."""
    try:
        shutil.copy2(backup_path, file_path)
        Path(backup_path).unlink()
        return True
    except OSError as e:
        logger.error(f"Failed to restore {file_path} from backup after a write error: {e}. Backup preserved at {backup_path}")
        return False


def discard_backup(backup_path: str) -> None:
    """Remove a backup after a successful write."""
    Path(backup_path).unlink(missing_ok=True)


def write_artwork_with_backup(file_path: str, role: str, image_bytes: Any, role_to_type: dict[str, int], mutate: Callable[[], bool], error_context: str) -> bool:
    """Back up the file, run the format's mutate(), verify the artwork, and restore the backup on any failure."""
    # mutate() returns False when there is nothing to write against; no verify is needed then.
    if role not in role_to_type:
        raise ValueError(f"Unknown artwork role: {role}")

    if not Path(file_path).exists():
        logger.debug(f"Skipping artwork write - file not found: {file_path}")
        return False

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
            logger.error(f"Artwork write verification failed for {file_path} (role={role}); attempted to restore backup")
            return False

        discard_backup(backup_path)
        return True

    except (OSError, ValueError, struct.error, Image.DecompressionBombError) as e:
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
