"""Helpers for obscuring album art marked as containing explicit imagery."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from src.core.logger_config import logger


def blur_pixmap(pixmap: QPixmap, strength: int = 12) -> QPixmap:
    """Return a heavily obscured copy of `pixmap`.

    Downscales then upscales the image so fine detail is destroyed — a cheap
    stand-in for a gaussian blur that doesn't require a live widget/scene to
    render, so it works equally well on thumbnails and full-size art.
    """
    if pixmap.isNull():
        return pixmap

    w, h = pixmap.width(), pixmap.height()
    small_w = max(1, w // strength)
    small_h = max(1, h // strength)
    small = pixmap.scaled(
        small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _blur_enabled() -> bool:
    app = QApplication.instance()
    display = getattr(app, "display_settings", None)
    return bool(getattr(display, "blur_explicit_art", False))


def load_art_pixmap(
    path: str | None, is_explicit: bool = False, strength: int = 12
) -> QPixmap:
    """Load a QPixmap from `path`, blurring it if the art is marked explicit
    and the "blur explicit album art" display option is enabled.

    Returns a null QPixmap if `path` is missing/invalid, mirroring QPixmap's
    own behavior so callers can fall back to a placeholder as usual.
    """
    if not path or not Path(path).exists():
        logger.debug(f"Art path missing or does not exist: {path}")
        return QPixmap()

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        logger.warning(f"Failed to load album art image: {path}")
        return pixmap

    if is_explicit and _blur_enabled():
        return blur_pixmap(pixmap, strength)

    return pixmap
