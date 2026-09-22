"""MusicBrainz-link and track-count filters on the Artist view."""

from src.artist.view import artist_view as artist_view_module
from src.artist.view.artist_view import ArtistView


class StubArtist:
    def __init__(self, artist_id, artist_name, mbid=None, isgroup=False):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.isgroup = isgroup
        self.first_pass = False
        self.second_pass = False
        self.profile_pic_path = None
        self.begin_year = None
        self.MBID = mbid
        self.types = []


class StubScalars:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class StubResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return StubScalars(self._values)


class StubSession:
    def __init__(self, artist_ids_with_tracks):
        self._artist_ids_with_tracks = artist_ids_with_tracks

    def execute(self, _stmt):
        return StubResult(self._artist_ids_with_tracks)


class StubGetController:
    def __init__(self, artists, artist_ids_with_tracks=()):
        self._artists = artists
        self.session = StubSession(artist_ids_with_tracks)

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._artists)


class StubController:
    def __init__(self, artists, artist_ids_with_tracks=()):
        self.get = StubGetController(artists, artist_ids_with_tracks)


class FakeAppConfig:
    def __init__(self, initial=None):
        self._filters = dict(initial or {})

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        pass


ARTISTS = [StubArtist(1, "Linked", mbid="abc-123"), StubArtist(2, "Unlinked", mbid=None), StubArtist(3, "AlsoLinked", mbid="def-456")]


def _make_view(monkeypatch, artists=ARTISTS, artist_ids_with_tracks=()):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfig())
    view = ArtistView(StubController(artists, artist_ids_with_tracks))
    view.show()
    return view


def _displayed_names(view):
    names = []
    for i in range(view.artist_list.count()):
        text = view.artist_list.item(i).text()
        names.append(text.replace(" \U0001f517", "").replace("👥 ", ""))
    return names


def test_has_link_shows_only_linked_artists(qapp, monkeypatch):
    view = _make_view(monkeypatch)
    view.link_combo.setCurrentText("Has Link")
    assert set(_displayed_names(view)) == {"Linked", "AlsoLinked"}


def test_no_link_shows_only_unlinked_artists(qapp, monkeypatch):
    view = _make_view(monkeypatch)
    view.link_combo.setCurrentText("No Link")
    assert _displayed_names(view) == ["Unlinked"]


def test_any_link_shows_all_artists(qapp, monkeypatch):
    view = _make_view(monkeypatch)
    view.link_combo.setCurrentText("Has Link")
    view.link_combo.setCurrentText("Any Link")
    assert set(_displayed_names(view)) == {"Linked", "Unlinked", "AlsoLinked"}


def test_has_tracks_shows_only_artists_with_tracks(qapp, monkeypatch):
    view = _make_view(monkeypatch, artist_ids_with_tracks=[1, 3])
    view.tracks_combo.setCurrentText("Has Tracks")
    assert set(_displayed_names(view)) == {"Linked", "AlsoLinked"}


def test_no_tracks_shows_only_artists_without_tracks(qapp, monkeypatch):
    view = _make_view(monkeypatch, artist_ids_with_tracks=[1, 3])
    view.tracks_combo.setCurrentText("No Tracks")
    assert _displayed_names(view) == ["Unlinked"]


def test_link_and_tracks_filters_persist(qapp, monkeypatch):
    fake_config = FakeAppConfig({"link": "Has Link", "tracks": "No Tracks"})
    monkeypatch.setattr(artist_view_module, "app_config", fake_config)
    view = ArtistView(StubController(ARTISTS))
    view.show()
    try:
        assert view.link_combo.currentText() == "Has Link"
        assert view.tracks_combo.currentText() == "No Tracks"
    finally:
        view.close()
