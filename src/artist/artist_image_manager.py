"""Manages moving picked artist profile pictures into the managed images directory."""

import re
import shutil
from pathlib import Path

from src.core.asset_paths import ARTIST_IMAGES_DIR
from src.core.logger_config import logger

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def move_to_artist_images_dir(artist_id, artist_name: str, source_path: str) -> str:
    """Move a picked image file into ARTIST_IMAGES_DIR and return its new path.

    Uses a deterministic filename (artist_id + sanitized name) so re-picking
    a picture for the same artist overwrites the previous file. If the file
    already lives inside ARTIST_IMAGES_DIR (e.g. re-saving without changing
    the picture), it is left in place and its path is returned unchanged.
    """
    src = Path(source_path)
    ARTIST_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    if src.resolve().parent == ARTIST_IMAGES_DIR.resolve():
        return str(src)

    sanitized_name = _INVALID_CHARS.sub("_", artist_name or "")
    filename = f"{artist_id}_{sanitized_name}{src.suffix}"
    dest = ARTIST_IMAGES_DIR / filename

    shutil.move(str(src), str(dest))
    logger.info(f"Moved artist image for {artist_id} to {dest}")
    return str(dest)
