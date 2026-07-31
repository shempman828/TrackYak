"""Regression test for #254: Artist view filter/search settings should be
restored on the next session and persisted whenever they change.
"""

from src.artist import artist_view as artist_view_module
from src.artist.artist_view import ArtistView


class StubArtist:
    def __init__(self, artist_id, artist_name, isgroup=False, types=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.isgroup = isgroup
        self.is_fixed = False
        self.profile_pic_path = None
        self.begin_year = None
        self.types = types or []


class StubArtistType:
    def __init__(self, type_name):
        self.type_name = type_name


class StubGetController:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._artists)


class StubController:
    def __init__(self, artists):
        self.get = StubGetController(artists)


class FakeAppConfig:
    """In-memory stand-in for app_config so tests never touch config.ini."""

    def __init__(self, initial=None):
        self._filters = dict(initial or {})
        self.save_calls = 0

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        self.save_calls += 1


def _make_view(monkeypatch, artists, fake_config):
    monkeypatch.setattr(artist_view_module, "app_config", fake_config)
    view = ArtistView(StubController(artists))
    view.show()
    return view


def test_restores_filters_from_previous_session(qapp, monkeypatch):
    fake_config = FakeAppConfig({
        "mode_text": "Groups",
        "search": "band",
        "sort": "Name (Z–A)",
        "metadata": "Fixed Only",
        "image": "Has Image",
        "type": "Rock",
    })
    artists = [
        StubArtist(1, "Alpha", isgroup=True, types=[StubArtistType("Rock")]),
        StubArtist(2, "Beta", isgroup=False),
    ]
    view = _make_view(monkeypatch, artists, fake_config)
    try:
        assert view.mode_combo.currentText() == "Groups"
        assert view.current_mode == "groups"
        assert view.search_box.text() == "band"
        assert view.sort_combo.currentText() == "Name (Z–A)"
        assert view.metadata_combo.currentText() == "Fixed Only"
        assert view.image_combo.currentText() == "Has Image"
        assert view.type_combo.currentText() == "Rock"
    finally:
        view.close()


def test_no_persisted_state_keeps_defaults(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    artists = [StubArtist(1, "Alpha")]
    view = _make_view(monkeypatch, artists, fake_config)
    try:
        assert view.mode_combo.currentText() == "All"
        assert view.search_box.text() == ""
        assert view.type_combo.currentText() == "Any Type"
    finally:
        view.close()


def test_filter_change_persists_to_config(qapp, monkeypatch):
    fake_config = FakeAppConfig()
    artists = [StubArtist(1, "Alpha")]
    view = _make_view(monkeypatch, artists, fake_config)
    try:
        view.search_box.setText("gamma")
        view.metadata_combo.setCurrentText("Fixed Only")

        view._filter_save_timer.stop()
        view._save_filter_state()

        saved = fake_config.get_artist_view_filters()
        assert saved["search"] == "gamma"
        assert saved["metadata"] == "Fixed Only"
        assert fake_config.save_calls >= 1
    finally:
        view.close()
