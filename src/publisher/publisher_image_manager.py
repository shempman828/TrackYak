"""Manages moving picked publisher logos into the managed images directory."""

from pathlib import Path
import re
import shutil

from src.foundation.asset_paths import PUBLISHER_LOGOS_DIR
from src.foundation.logger_config import logger

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def move_to_publisher_logos_dir(publisher_id, publisher_name: str, source_path: str) -> str:
    """Move a picked image file into PUBLISHER_LOGOS_DIR and return its new path.

    Uses a deterministic filename (publisher_id + sanitized name) so
    re-picking a logo for the same publisher overwrites the previous file.
    If the file already lives inside PUBLISHER_LOGOS_DIR (e.g. re-saving
    without changing the logo), it is left in place and its path is
    returned unchanged.
    """
    src = Path(source_path)
    PUBLISHER_LOGOS_DIR.mkdir(parents=True, exist_ok=True)

    if src.resolve().parent == PUBLISHER_LOGOS_DIR.resolve():
        return str(src)

    sanitized_name = _INVALID_CHARS.sub("_", publisher_name or "")
    filename = f"{publisher_id}_{sanitized_name}{src.suffix}"
    dest = PUBLISHER_LOGOS_DIR / filename

    shutil.move(str(src), str(dest))
    logger.info(f"Moved publisher logo for {publisher_id} to {dest}")
    return str(dest)
