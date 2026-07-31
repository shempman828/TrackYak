"""Regression test for #251: an album edited so it no longer satisfies the
active grid filter (e.g. is_fixed flips while filtering "Not Fixed") must be
removed from the grid in place, without a full grid rebuild.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from src.album.album_view import AlbumView


class StubAlbumWidget(QWidget):
    clicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album

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
