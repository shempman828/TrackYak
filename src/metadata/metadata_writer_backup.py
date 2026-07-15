"""Shared backup/restore/verify helpers used by the MP3 and FLAC file
writers when writing tags or artwork.
"""

import hashlib
import os
import shutil
from typing import Any

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
