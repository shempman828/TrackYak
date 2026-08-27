"""Regression tests for bug #235: two albums sharing a grid slot on first load.

Root cause: a freshly reparented widget's "show" is deferred to the next
event-loop turn, so QWidget.isVisible() is still False if a layout pass runs
synchronously right after addWidget(). FlowLayout._do_layout() skips
positioning any item whose widget reports isVisible() == False, so such a
widget is left stuck at its default geometry -- overlapping whatever sits at
slot (0, 0) -- until some later, fully-settled layout pass (e.g. the one
triggered by scrolling) recomputes every widget's position from scratch.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from src.album.album_flowlayout import FlowLayout
from src.album.album_view import AlbumView


def test_flow_layout_skips_unshown_widget_on_synchronous_layout_pass(qapp):
    """Documents the underlying FlowLayout hazard the fix works around."""
    parent = QWidget()
    parent.resize(600, 400)
    parent.show()
    layout = FlowLayout(parent)

    shown = QWidget(parent)
    shown.setFixedSize(50, 50)
    layout.addWidget(shown)
    shown.show()

    unshown = QWidget(parent)
    unshown.setFixedSize(50, 50)
    layout.addWidget(unshown)
    # Note: unshown.show() is deliberately NOT called here -- its "shown"
    # state hasn't propagated yet, mirroring a widget added moments before
    # a forced synchronous layout/repaint pass.

    layout.setGeometry(parent.rect())

    # The unshown widget was skipped by _do_layout()'s isVisible() check, so
    # it never received a real geometry and is left overlapping the first
    # slot instead of taking its rightful second-in-row position -- this is
    # the "two albums in one grid slot" symptom from bug #235.
    assert unshown.geometry() == shown.geometry()

    # Once the widget is actually visible, the next full layout pass (e.g.
    # the one triggered by scrolling in AlbumView) positions it correctly --
    # matching the observed "self-heals after the first scroll" behavior.
    unshown.show()
    layout.invalidate()
    layout.setGeometry(parent.rect())

    assert unshown.geometry() != shown.geometry()


def test_add_album_widget_shows_widget_immediately(qapp, monkeypatch):
    """AlbumView._add_album_widget must show() each widget synchronously so
    the very next layout pass (even one forced before the event loop has
    caught up) positions it correctly instead of skipping it.
    """

    class StubAlbumWidget(QWidget):
        clicked = Signal(object)

        def __init__(self, album, size=200, parent=None):
            super().__init__(parent)
            self.album = album

    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget)

    class StubGetController:
        def get_all_entities(self, entity_type, load_options=None):
            return []

    class StubController:
        def __init__(self):
            self.get = StubGetController()

    view = AlbumView(StubController())
    try:
        # isVisible() reflects the whole ancestor chain, so the view itself
        # must already be shown -- exactly as it is in production by the
        # time main_window.py adds it to the (visible) stacked widget and
        # forces a synchronous repaint().
        view.show()

        view._add_album_widget(object())

        added = view.grid_layout.itemAt(view.grid_layout.count() - 1).widget()
        # No qapp.processEvents() call here: the assertion must hold
        # immediately, without waiting for a future event-loop turn.
        assert added.isVisible() is True
    finally:
        view.close()
