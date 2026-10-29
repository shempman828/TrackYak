"""AlbumWidget click handling and placeholder caching."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.album.view.album_widget import AlbumFlowWidget, AlbumWidget


def _album(**kw):
    return SimpleNamespace(album_name="Blue", album_artist_names="Joni Mitchell", **kw)


def test_double_click_emits_double_clicked_once_and_no_single_click(qapp):
    widget = AlbumWidget(_album(), 100)
    widget.show()
    doubles, singles = [], []
    widget.doubleClicked.connect(doubles.append)
    widget.clicked.connect(singles.append)

    QTest.mouseClick(widget, Qt.LeftButton)
    QTest.mouseDClick(widget, Qt.LeftButton)
    QTest.qWait(widget.click_timer.interval() + 50)

    assert len(doubles) == 1
    assert singles == []


def test_placeholder_pixmap_is_shared_between_tiles(qapp):
    first = AlbumWidget(_album(), 100)
    second = AlbumWidget(_album(), 100)
    assert first._load_art() is second._load_art()


def test_missing_artist_names_fall_back_to_unknown(qapp):
    widget = AlbumWidget(SimpleNamespace(album_name="X", album_artist_names=None), 100)
    assert widget.artist_label.text() == "Unknown Artist"


def test_flow_widget_keeps_qwidget_layout_accessor(qapp):
    flow = AlbumFlowWidget([_album()], album_size=80)
    assert flow.layout() is flow._grid
