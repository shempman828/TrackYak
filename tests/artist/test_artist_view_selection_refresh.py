"""Regression test: editing an artist to flip Person <-> Group must refresh
the detail panel's Membership section, not leave it showing stale data.

Bug: `_populate_list` rebuilt the artist list from scratch on every reload
(e.g. after `_edit_artist` calls `load_artists()`), which silently dropped
the current selection. `_on_artist_selected` then found no current item and
returned early, so the detail panel -- including `MembershipWidget` -- kept
showing the pre-edit artist even though the underlying record had changed.
"""

from PySide6.QtCore import Qt

from src.artist.view import artist_view as artist_view_module
from src.artist.view.artist_view import ArtistView


class StubArtist:
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


class StubGetController:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, *args, **kwargs):
        entity_type = args[0] if args else kwargs.get("entity_type")
        if entity_type == "Artist":
            return list(self._artists)
        return []

    def get_entity_object(self, entity_type, artist_id=None, **kwargs):
        return next((a for a in self._artists if a.artist_id == artist_id), None)


class StubController:
    def __init__(self, artists):
        self.get = StubGetController(artists)


class FakeAppConfig:
    def __init__(self):
        self._filters = {}

    def get_artist_view_filters(self):
        return dict(self._filters)

    def set_artist_view_filters(self, filters):
        self._filters = dict(filters)

    def save(self):
        pass


def _make_view(monkeypatch, artists):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfig())
    view = ArtistView(StubController(artists))
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
    person = StubArtist(1, "Solo Artist", isgroup=False)
    other = StubArtist(2, "Someone Else", isgroup=False)
    view = _make_view(monkeypatch, [person, other])

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
