"""Shared pixmap loading with a placeholder-on-missing/invalid fallback."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from src.foundation.asset_paths import asset
from src.image.image_blur import load_art_pixmap

_DEFAULT_PORTRAIT = asset("default_artist.svg")
_renderer: QSvgRenderer | None = None


def _portrait_renderer() -> QSvgRenderer:
    """Return the shared default-portrait SVG renderer, parsing it on first use."""
    global _renderer
    if _renderer is None:
        _renderer = QSvgRenderer(_DEFAULT_PORTRAIT)
    return _renderer


def load_pixmap_with_fallback(path: str | None, size: QSize) -> QPixmap:
    """Load `path` scaled to fit within `size` (aspect kept), or render the default artist silhouette."""
    pixmap = load_art_pixmap(path)
    if not pixmap.isNull():
        return pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    renderer = _portrait_renderer()
    if not renderer.isValid():
        return QPixmap()

    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    fallback = QPixmap(size * dpr)
    fallback.setDevicePixelRatio(dpr)
    fallback.fill(Qt.transparent)

    # Center the silhouette at its own aspect ratio; a non-square box would stretch it.
    target = renderer.defaultSize().scaled(size, Qt.KeepAspectRatio)
    x = (size.width() - target.width()) / 2
    y = (size.height() - target.height()) / 2
    painter = QPainter(fallback)
    renderer.render(painter, QRectF(x, y, target.width(), target.height()))
    painter.end()
    return fallback
