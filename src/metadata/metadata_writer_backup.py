"""Shared backup/restore/verify helpers used by the MP3 and FLAC file
writers when writing tags or artwork.
"""

import hashlib
import os
import shutil
from typing import Any, Callable, Dict

from src.core.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor


def backup_file(file_path: str) -> str:
    """Copy file_path to a sibling .bak file and return its path."""
    backup_path = file_path + ".bak"
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
        os.remove(backup_path)
        return True
    except Exception as e:
        logger.error(
            f"Failed to restore {file_path} from backup after a write "
            f"error: {e}. Backup preserved at {backup_path}"
        )
        return False


def discard_backup(backup_path: str) -> None:
    """Remove a backup after a successful write."""
    if os.path.exists(backup_path):
        os.remove(backup_path)


def write_artwork_with_backup(
    file_path: str,
    role: str,
    image_bytes: Any,
    role_to_type: Dict[str, int],
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

    except Exception as e:
        logger.debug(f"Error writing {error_context} to {file_path}: {e}")
        if backup_path and os.path.exists(backup_path):
            restore_backup(file_path, backup_path)
        return False


def verify_artwork_write(file_path: str, role: str, image_bytes: Any) -> bool:
    """Re-read the file and confirm the write did what it was meant to do."""
    ext = os.path.splitext(file_path)[1].lower()
    result = ArtworkExtractor().extract_artwork_by_role(file_path, ext)

    if image_bytes is None:
        return role not in result

    picture = result.get(role)
    if not picture:
        return False

    return hashlib.sha256(picture["data"]).digest() == hashlib.sha256(
        image_bytes
    ).digest()
