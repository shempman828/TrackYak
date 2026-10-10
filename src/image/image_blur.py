"""Helpers for obscuring album art marked as containing explicit imagery."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from src.foundation.logger_config import logger


def blur_pixmap(pixmap: QPixmap, strength: int = 12) -> QPixmap:
    """Return a heavily obscured copy of `pixmap` by downscaling then upscaling it."""
    if pixmap.isNull():
        return pixmap

    strength = max(1, strength)  # 0 or less would divide by zero
    w, h = pixmap.width(), pixmap.height()
    small_w = max(1, w // strength)
    small_h = max(1, h // strength)
    small = pixmap.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def blur_enabled() -> bool:
    """Return True if the "blur explicit album art" display option is on."""
    app = QApplication.instance()
    display = getattr(app, "display_settings", None)
    return bool(getattr(display, "blur_explicit_art", False))


def load_art_pixmap(path: str | None, is_explicit: bool = False, strength: int = 12) -> QPixmap:
    """Load a QPixmap from `path`, blurred if explicit and enabled; a null QPixmap if missing or invalid."""
    if not path or not Path(path).exists():
        logger.debug(f"Art path missing or does not exist: {path}")
        return QPixmap()

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        logger.warning(f"Failed to load album art image: {path}")
        return pixmap

    if is_explicit and blur_enabled():
        return blur_pixmap(pixmap, strength)

    return pixmap
