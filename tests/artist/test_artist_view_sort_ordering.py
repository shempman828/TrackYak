"""AC2, AC3, AC8 -- the Artist browser orders by filing name (Artist.sort_name)
while still displaying the plain artist_name.

See docs/specs/artist_sort_name_ordering.md.
"""

from src.artist import artist_view as artist_view_module
from src.artist.artist_view import ArtistView


class StubArtist:
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


class StubGetController:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, entity_type, load_options=None):
        return list(self._artists)


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


# "Beatles, The" < "Bee Gees" < "Waits, Tom" by filing name, which is a
# different order than the display names ("Bee Gees" < "The Beatles" < "Tom Waits").
ARTISTS = [
    StubArtist(1, "Tom Waits", "Waits, Tom"),
    StubArtist(2, "The Beatles", "Beatles, The"),
    StubArtist(3, "Bee Gees", "Bee Gees"),
]


def _make_view(monkeypatch):
    monkeypatch.setattr(artist_view_module, "app_config", FakeAppConfig())
    view = ArtistView(StubController(ARTISTS))
    view.show()
    return view


def _displayed_names(view):
    names = []
    for i in range(view.artist_list.count()):
        text = view.artist_list.item(i).text()
        names.append(text.replace(" \U0001f517", "").replace("👥 ", ""))
    return names


def test_initial_load_is_filing_name_order(qapp, monkeypatch):
    """AC3 -- before any sort-combo interaction the list is already filed by sort_name."""
    view = _make_view(monkeypatch)
    assert _displayed_names(view) == ["The Beatles", "Bee Gees", "Tom Waits"]


# The Name sort labels carry an en dash; pull them from the widget rather
# than retyping the literal so this test stays in step with the source.
_NAME_AZ = ArtistView._SORT_OPTIONS[0][0]
_NAME_ZA = ArtistView._SORT_OPTIONS[1][0]


def test_name_az_uses_filing_name(qapp, monkeypatch):
    """AC2 -- the ascending Name sort keys off sort_name."""
    view = _make_view(monkeypatch)
    view.sort_combo.setCurrentText(_NAME_AZ)
    assert _displayed_names(view) == ["The Beatles", "Bee Gees", "Tom Waits"]


def test_name_za_is_exact_reverse(qapp, monkeypatch):
    """AC2 -- the descending Name sort reverses the same filing-name key."""
    view = _make_view(monkeypatch)
    view.sort_combo.setCurrentText(_NAME_ZA)
    assert _displayed_names(view) == ["Tom Waits", "Bee Gees", "The Beatles"]


def test_display_text_is_plain_artist_name(qapp, monkeypatch):
    """AC8 -- rows render artist_name, never the sort name."""
    view = _make_view(monkeypatch)
    shown = _displayed_names(view)
    assert "Beatles, The" not in shown
    assert "Waits, Tom" not in shown
    assert set(shown) == {"The Beatles", "Bee Gees", "Tom Waits"}
