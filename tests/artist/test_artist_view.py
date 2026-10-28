"""ArtistView: filter persistence, link/track filters, filing-name sort
order, and selection across reloads.
"""

from PySide6.QtCore import Qt

from src.artist.view import artist_view as artist_view_module
from src.artist.view.artist_view import ArtistView

# ---------------------------------------------------------------------------
# Regression test for #254: Artist view filter/search settings should be
# ---------------------------------------------------------------------------


class StubArtist:
    def __init__(self, artist_id, artist_name, isgroup=False, types=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.isgroup = isgroup
        self.first_pass = False
        self.second_pass = False
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
        "metadata": "Second Pass",
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
        assert view.metadata_combo.currentText() == "Second Pass"
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
        view.metadata_combo.setCurrentText("Second Pass")

        view._filter_save_timer.stop()
        view._save_filter_state()

        saved = fake_config.get_artist_view_filters()
        assert saved["search"] == "gamma"
        assert saved["metadata"] == "Second Pass"
        assert fake_config.save_calls >= 1
    finally:
        view.close()


# ---------------------------------------------------------------------------
# MusicBrainz-link and track-count filters on the Artist view.
# ---------------------------------------------------------------------------


class StubArtistFilters:
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


class StubGetControllerFilters:
    def __init__(self, artists, artist_ids_with_tracks=()):
        self._artists = artists
        self.session = StubSession(artist_ids_with_tracks)

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._artists)


class StubControllerFilters:
    def __init__(self, artists, artist_ids_with_tracks=()):
        self.get = StubGetControllerFilters(artists, artist_ids_with_tracks)


class FakeAppConfigFilters:
    def __init__(self, initial=None):
        self._filters = dict(initial or {})

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        pass


ARTISTS = [StubArtistFilters(1, "Linked", mbid="abc-123"), StubArtistFilters(2, "Unlinked", mbid=None), StubArtistFilters(3, "AlsoLinked", mbid="def-456")]


def _make_view_filters(monkeypatch, artists=ARTISTS, artist_ids_with_tracks=()):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfigFilters())
    view = ArtistView(StubControllerFilters(artists, artist_ids_with_tracks))
    view.show()
    return view


def _displayed_names(view):
    names = []
    for i in range(view.artist_list.count()):
        text = view.artist_list.item(i).text()
        names.append(text.replace(" \U0001f517", "").replace("👥 ", ""))
    return names


def test_has_link_shows_only_linked_artists(qapp, monkeypatch):
    view = _make_view_filters(monkeypatch)
    view.link_combo.setCurrentText("Has Link")
    assert set(_displayed_names(view)) == {"Linked", "AlsoLinked"}


def test_no_link_shows_only_unlinked_artists(qapp, monkeypatch):
    view = _make_view_filters(monkeypatch)
    view.link_combo.setCurrentText("No Link")
    assert _displayed_names(view) == ["Unlinked"]


def test_any_link_shows_all_artists(qapp, monkeypatch):
    view = _make_view_filters(monkeypatch)
    view.link_combo.setCurrentText("Has Link")
    view.link_combo.setCurrentText("Any Link")
    assert set(_displayed_names(view)) == {"Linked", "Unlinked", "AlsoLinked"}


def test_has_tracks_shows_only_artists_with_tracks(qapp, monkeypatch):
    view = _make_view_filters(monkeypatch, artist_ids_with_tracks=[1, 3])
    view.tracks_combo.setCurrentText("Has Tracks")
    assert set(_displayed_names(view)) == {"Linked", "AlsoLinked"}


def test_no_tracks_shows_only_artists_without_tracks(qapp, monkeypatch):
    view = _make_view_filters(monkeypatch, artist_ids_with_tracks=[1, 3])
    view.tracks_combo.setCurrentText("No Tracks")
    assert _displayed_names(view) == ["Unlinked"]


def test_link_and_tracks_filters_persist(qapp, monkeypatch):
    fake_config = FakeAppConfigFilters({"link": "Has Link", "tracks": "No Tracks"})
    monkeypatch.setattr(artist_view_module, "app_config", fake_config)
    view = ArtistView(StubControllerFilters(ARTISTS))
    view.show()
    try:
        assert view.link_combo.currentText() == "Has Link"
        assert view.tracks_combo.currentText() == "No Tracks"
    finally:
        view.close()


# ---------------------------------------------------------------------------
# AC2, AC3, AC8 -- the Artist browser orders by filing name (Artist.sort_name)
# ---------------------------------------------------------------------------


class StubArtistOrdering:
    def __init__(self, artist_id, artist_name, sort_name=None, isgroup=False):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = sort_name
        self.isgroup = isgroup
        self.first_pass = False
        self.second_pass = False
        self.profile_pic_path = None
        self.begin_year = None
        self.MBID = None
        self.types = []


class StubGetControllerOrdering:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._artists)


class StubControllerOrdering:
    def __init__(self, artists):
        self.get = StubGetControllerOrdering(artists)


class FakeAppConfigOrdering:
    def __init__(self):
        self._filters = {}

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        pass


# "Beatles, The" < "Bee Gees" < "Waits, Tom" by filing name, which is a
# different order than the display names ("Bee Gees" < "The Beatles" < "Tom Waits").
ARTISTS_ORDERING = [
    StubArtistOrdering(1, "Tom Waits", "Waits, Tom"),
    StubArtistOrdering(2, "The Beatles", "Beatles, The"),
    StubArtistOrdering(3, "Bee Gees", "Bee Gees"),
]


def _make_view_ordering(monkeypatch):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfigOrdering())
    view = ArtistView(StubControllerOrdering(ARTISTS_ORDERING))
    view.show()
    return view


def _displayed_names_ordering(view):
    names = []
    for i in range(view.artist_list.count()):
        text = view.artist_list.item(i).text()
        names.append(text.replace(" \U0001f517", "").replace("👥 ", ""))
    return names


def test_initial_load_is_filing_name_order(qapp, monkeypatch):
    """AC3 -- before any sort-combo interaction the list is already filed by sort_name."""
    view = _make_view_ordering(monkeypatch)
    assert _displayed_names_ordering(view) == ["The Beatles", "Bee Gees", "Tom Waits"]


# The Name sort labels carry an en dash; pull them from the widget rather
# than retyping the literal so this test stays in step with the source.
_NAME_AZ = ArtistView._SORT_OPTIONS[0][0]


_NAME_ZA = ArtistView._SORT_OPTIONS[1][0]


def test_name_az_uses_filing_name(qapp, monkeypatch):
    """AC2 -- the ascending Name sort keys off sort_name."""
    view = _make_view_ordering(monkeypatch)
    view.sort_combo.setCurrentText(_NAME_AZ)
    assert _displayed_names_ordering(view) == ["The Beatles", "Bee Gees", "Tom Waits"]


def test_name_za_is_exact_reverse(qapp, monkeypatch):
    """AC2 -- the descending Name sort reverses the same filing-name key."""
    view = _make_view_ordering(monkeypatch)
    view.sort_combo.setCurrentText(_NAME_ZA)
    assert _displayed_names_ordering(view) == ["Tom Waits", "Bee Gees", "The Beatles"]


def test_display_text_is_plain_artist_name(qapp, monkeypatch):
    """AC8 -- rows render artist_name, never the sort name."""
    view = _make_view_ordering(monkeypatch)
    shown = _displayed_names_ordering(view)
    assert "Beatles, The" not in shown
    assert "Waits, Tom" not in shown
    assert set(shown) == {"The Beatles", "Bee Gees", "Tom Waits"}


# ---------------------------------------------------------------------------
# Regression test: editing an artist to flip Person <-> Group must refresh
# ---------------------------------------------------------------------------


class StubArtistRefresh:
    def __init__(self, artist_id, artist_name, isgroup=False):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = artist_name
        self.isgroup = isgroup
        self.first_pass = False
        self.second_pass = False
        self.profile_pic_path = None
        self.begin_year = None
        self.end_year = None
        self.begin_month = None
        self.end_month = None
        self.begin_day = None
        self.end_day = None
        self.places = []
        self.MBID = None
        self.types = []
        self.albums = []
        self.group_memberships = []
        self.member_memberships = []


class StubGetControllerRefresh:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, *args, **kwargs):
        entity_type = args[0] if args else kwargs.get("entity_type")
        if entity_type == "Artist":
            return list(self._artists)
        return []

    def get_entity_object(self, entity_type, artist_id=None, **kwargs):
        return next((a for a in self._artists if a.artist_id == artist_id), None)


class StubControllerRefresh:
    def __init__(self, artists):
        self.get = StubGetControllerRefresh(artists)


class FakeAppConfigRefresh:
    def __init__(self):
        self._filters = {}

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        pass


def _make_view_refresh(monkeypatch, artists):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfigRefresh())
    view = ArtistView(StubControllerRefresh(artists))
    view.show()
    return view


def _select_artist(view, artist_id):
    for i in range(view.artist_list.count()):
        item = view.artist_list.item(i)
        if item.data(Qt.UserRole) == artist_id:
            view.artist_list.setCurrentItem(item)
            return
    raise AssertionError(f"artist {artist_id} not found in list")


def test_reload_preserves_selection_and_refreshes_detail_panel(qapp, monkeypatch):
    person = StubArtistRefresh(1, "Solo Artist", isgroup=False)
    other = StubArtistRefresh(2, "Someone Else", isgroup=False)
    view = _make_view_refresh(monkeypatch, [person, other])

    _select_artist(view, 1)
    assert view._current_detail.artist.isgroup is False

    # Simulate the ArtistEditor flipping this artist to a group, then the
    # same reload `_edit_artist` performs: `load_artists()`.
    person.isgroup = True
    view.load_artists()

    current = view.artist_list.currentItem()
    assert current is not None
    assert current.data(Qt.UserRole) == 1
    assert view._current_detail is not None
    assert view._current_detail.artist.isgroup is True
