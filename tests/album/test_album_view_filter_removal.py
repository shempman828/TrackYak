"""Regression test for #251: an album edited so it no longer satisfies the
active grid filter (e.g. is_fixed flips while filtering "Not Fixed") must be
removed from the grid in place, without a full grid rebuild.

Also covers #260: that same in-place removal must not leave lazy-loading
stuck. It used to read the scroll bar's maximum synchronously right after
removing the widget, but QScrollArea only recomputes that range on a later
event-loop turn, so the read was always stale. When the removal happened to
make the grid exactly fill the viewport (no more scrollbar), the stale read
still saw the old, positive maximum and skipped topping up the view -- and
with no scrollbar left to scroll, nothing could ever trigger loading the
remaining filtered albums again.
"""

from PySide6.QtCore import Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from src.album.album_view import AlbumView


class StubAlbumWidget(QWidget):
    clicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album
        self.setFixedSize(900, 150)

    def refresh_album(self, album):
        self.album = album


class StubAlbum:
    def __init__(self, album_id, album_name, is_fixed=False):
        self.album_id = album_id
        self.album_name = album_name
        self.is_fixed = is_fixed
        self.release_year = None
        self.possibly_incomplete = False


class StubGetController:
    def __init__(self, albums_by_id):
        self._albums_by_id = albums_by_id

    def get_all_entities(self, entity_type):
        return list(self._albums_by_id.values())

    def get_entity_object(self, entity_type, album_id):
        return self._albums_by_id[album_id]


class StubController:
    def __init__(self, albums_by_id):
        self.get = StubGetController(albums_by_id)


def _make_view(monkeypatch, albums):
    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget)
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController(albums_by_id))
    view.show()
    view.load_albums()
    return view, albums_by_id


def test_edit_that_fails_active_filter_removes_widget_without_full_reload(
    qapp, monkeypatch
):
    albums = [StubAlbum(1, "Alpha", is_fixed=False), StubAlbum(2, "Beta", is_fixed=False)]
    view, albums_by_id = _make_view(monkeypatch, albums)
    try:
        view.fixed_combo.setCurrentText("Not Fixed")
        assert [a.album_id for a in view.filtered_albums] == [1, 2]
        assert view.grid_layout.count() == 2

        # Neither full-reload path should fire for this edit.
        def _fail(*args, **kwargs):
            raise AssertionError("full grid reload should not be triggered")

        monkeypatch.setattr(view, "load_albums", _fail)
        monkeypatch.setattr(view, "_apply_filters_preserve_scroll", _fail)

        # Simulate the edit: album 1 becomes fixed, no longer matching
        # the "Not Fixed" filter.
        albums_by_id[1].is_fixed = True
        view._patch_album_after_edit(1)

        assert [a.album_id for a in view.filtered_albums] == [2]
        assert view.grid_layout.count() == 1
        remaining = view.grid_layout.itemAt(0).widget()
        assert remaining.album.album_id == 2
    finally:
        view.close()


def test_edit_that_still_matches_filter_keeps_widget(qapp, monkeypatch):
    albums = [StubAlbum(1, "Alpha", is_fixed=False), StubAlbum(2, "Beta", is_fixed=False)]
    view, albums_by_id = _make_view(monkeypatch, albums)
    try:
        view.fixed_combo.setCurrentText("Not Fixed")
        assert view.grid_layout.count() == 2

        albums_by_id[1].album_name = "Alpha Renamed"
        view._patch_album_after_edit(1)

        assert [a.album_id for a in view.filtered_albums] == [1, 2]
        assert view.grid_layout.count() == 2
    finally:
        view.close()


def test_removal_that_fills_viewport_does_not_strand_remaining_albums(
    qapp, monkeypatch
):
    """#260: if the in-place removal happens to make the grid exactly fill
    the viewport (no scrollbar left), the filtered albums beyond the display
    window must still eventually load via the deferred viewport-fill check,
    not get stuck forever.
    """
    monkeypatch.setattr("src.album.album_view.AlbumWidget", StubAlbumWidget)

    # 12 albums, zero-padded names so title-sort order matches album_id order.
    albums = [
        StubAlbum(i, f"Album {i:03d}", is_fixed=False) for i in range(1, 13)
    ]
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController(albums_by_id))
    try:
        view.load_chunk = 10  # display_count starts at 10, leaving 2 unloaded

        view.resize(1100, 900)
        view.show()
        qapp.processEvents()

        # 10 rows of the 900x150 stub widgets (150 + 20 v-spacing each) need
        # 1680px; 9 rows need 1510px. Fixing the viewport at 1520px means
        # showing 10 albums overflows (real scrollbar) but showing 9 exactly
        # fits (no scrollbar) -- the exact edge case that exposed the bug.
        view.scroll_area.setFixedHeight(1520)
        qapp.processEvents()

        view.load_albums()
        QTest.qWait(150)

        view.fixed_combo.setCurrentText("Not Fixed")
        QTest.qWait(150)
        assert view.display_count == 10
        assert len(view.filtered_albums) == 12

        # Edit the last currently-displayed album so it drops out of the
        # "Not Fixed" filter, shrinking the grid down to exactly 9 rows.
        target = view.filtered_albums[9]
        albums_by_id[target.album_id].is_fixed = True
        view._patch_album_after_edit(target.album_id)

        assert len(view.filtered_albums) == 11
        assert view.display_count == 9

        QTest.qWait(300)

        assert view.display_count == len(view.filtered_albums) == 11
        assert view.grid_layout.count() == 11
    finally:
        view.close()
