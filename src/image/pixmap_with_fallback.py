"""Shared pixmap loading with a placeholder-on-missing/invalid fallback."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from src.core.asset_paths import asset
from src.image.image_blur import load_art_pixmap

_DEFAULT_PORTRAIT = asset("default_artist.svg")


def load_pixmap_with_fallback(path: str, size: QSize) -> QPixmap:
    """Load a QPixmap from `path`, scaled to fit within `size`.

    The image's own aspect ratio is preserved — `size` is a bounding box,
    not a target shape, so portrait or landscape photos are never
    stretched or cropped into a square. Falls back to rendering the
    default artist silhouette SVG when `path` is empty, missing, or fails
    to load as an image — callers don't need to special-case "no picture"
    themselves.
    """
    pixmap = load_art_pixmap(path)
    if not pixmap.isNull():
        return pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    renderer = QSvgRenderer(_DEFAULT_PORTRAIT)
    if renderer.isValid():
        fallback = QPixmap(size)
        fallback.fill(Qt.transparent)
        painter = QPainter(fallback)
        renderer.render(painter)
        painter.end()
        return fallback

    return QPixmap()
