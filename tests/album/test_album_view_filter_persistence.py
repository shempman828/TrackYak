"""Regression test for #254: Album view filter/search settings should be
restored on the next session and persisted whenever they change.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from src.album import album_filtering as album_filtering_module
from src.album import album_view as album_view_module
from src.album.album_view import AlbumView


class StubAlbumWidget(QWidget):
    clicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album

    def refresh_album(self, album):
        self.album = album


class StubAlbum:
    def __init__(self, album_id, album_name):
        self.album_id = album_id
        self.album_name = album_name
        self.release_year = None
        self.first_pass = False
        self.second_pass = False
        self.possibly_incomplete = False


class StubGetController:
    def __init__(self, albums_by_id):
        self._albums_by_id = albums_by_id

    def get_all_entities(self, entity_type):
        return list(self._albums_by_id.values())


class StubController:
    def __init__(self, albums_by_id):
        self.get = StubGetController(albums_by_id)


class FakeAppConfig:
    """In-memory stand-in for app_config so tests never touch config.ini."""

    def __init__(self, initial=None):
        self._filters = dict(initial or {})
        self.save_calls = 0

    def get_album_view_filters(self):
        return dict(self._filters)

    def set_album_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        self.save_calls += 1


def _make_view(monkeypatch, albums, fake_config):
    monkeypatch.setattr(album_view_module, "AlbumWidget", StubAlbumWidget)
    monkeypatch.setattr(album_filtering_module, "app_config", fake_config)
    albums_by_id = {a.album_id: a for a in albums}
    view = AlbumView(StubController(albums_by_id))
    view.show()
    return view


def test_restores_filters_from_previous_session(qapp, monkeypatch):
    fake_config = FakeAppConfig({
        "search": "alpha",
        "year_from": 1990,
        "year_to": 1999,
        "min_tracks": 5,
        "incomplete_mode": "Possibly Incomplete",
        "fixed_mode": "Second Pass",
        "art_mode": "Has Art",
    })
    albums = [StubAlbum(1, "Alpha")]
    view = _make_view(monkeypatch, albums, fake_config)
    try:
        assert view.search_bar.text() == "alpha"
        assert view.year_from.value() == 1990
        assert view.year_to.value() == 1999
        assert view.min_tracks.value() == 5
        assert view.incomplete_combo.currentText() == "Possibly Incomplete"
        assert view.fixed_combo.currentText() == "Second Pass"
        assert view.art_combo.currentText() == "Has Art"
    finally:
        view.close()


def test_no_persisted_state_keeps_defaults(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [StubAlbum(1, "Alpha")]
    view = _make_view(monkeypatch, albums, fake_config)
    try:
        assert view.search_bar.text() == ""
        assert view.year_from.value() == 0
        assert view.incomplete_combo.currentText() == "Any"
    finally:
        view.close()


def test_filter_change_persists_to_config(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    albums = [StubAlbum(1, "Alpha")]
    view = _make_view(monkeypatch, albums, fake_config)
    try:
        view.search_bar.setText("beta")
        view.fixed_combo.setCurrentText("Second Pass")

        # Force the debounced save to run immediately instead of waiting
        # out its 400ms timer.
        view._filter_save_timer.stop()
        view._save_filter_state()

        saved = fake_config.get_album_view_filters()
        assert saved["search"] == "beta"
        assert saved["fixed_mode"] == "Second Pass"
        assert fake_config.save_calls >= 1
    finally:
        view.close()
