"""Unit tests for src/image/image_blur.py and src/image/pixmap_with_fallback.py."""

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPixmap

from src.image.image_blur import blur_pixmap
from src.image.pixmap_with_fallback import load_pixmap_with_fallback


def test_blur_with_non_positive_strength_does_not_raise(qapp):
    pm = QPixmap(20, 10)
    pm.fill(QColor("red"))

    for strength in (0, -3):
        out = blur_pixmap(pm, strength)
        assert (out.width(), out.height()) == (20, 10)


def test_fallback_fills_requested_logical_size_for_missing_path(qapp):
    out = load_pixmap_with_fallback(None, QSize(200, 100))

    assert not out.isNull()
    dpr = out.devicePixelRatio()
    assert (round(out.width() / dpr), round(out.height() / dpr)) == (200, 100)


def test_fallback_keeps_silhouette_aspect_in_wide_box(qapp):
    out = load_pixmap_with_fallback("", QSize(300, 100)).toImage()

    # The silhouette is centered, so the far left and right edges stay transparent.
    mid_y = out.height() // 2
    assert out.pixelColor(0, mid_y).alpha() == 0
    assert out.pixelColor(out.width() - 1, mid_y).alpha() == 0
