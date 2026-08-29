"""Unit tests for the entity-completer secondary-context builders.

Each builder maps a list of ORM-ish rows to {entity_id: context string};
the string is shown dimmed beside the name in the completer popup and must
degrade gracefully (missing relationship / null field -> "", never an
exception).
"""

from src.common.entity_completer_context import (
    album_context_map,
    artist_context_map,
    place_context_map,
    publisher_context_map,
    track_context_map,
)


class _Artist:
    def __init__(self, artist_id, artist_name, **kw):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.disambiguation = kw.get("disambiguation")
        self.isgroup = kw.get("isgroup")
        self._span = kw.get("career_span")

    @property
    def career_span(self):
        return self._span


class _Place:
    def __init__(self, place_id, place_name, place_type=None, parent=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.parent = parent


class _ArtistName:
    def __init__(self, name):
        self.artist_name = name


class _Track:
    def __init__(self, track_id, primary_artists=(), album_name=None):
        self.track_id = track_id
        self.primary_artists = list(primary_artists)
        self.album_name = album_name


class _Album:
    def __init__(self, album_id, album_artists=(), release_year=None):
        self.album_id = album_id
        self.album_artists = list(album_artists)
        self.release_year = release_year


class _Publisher:
    def __init__(self, publisher_id, publisher_name, parent=None, begin_year=None, end_year=None):
        self.publisher_id = publisher_id
        self.publisher_name = publisher_name
        self.parent = parent
        self.begin_year = begin_year
        self.end_year = end_year


# ── artist_context_map (AC 4) ────────────────────────────────────────────


def test_artist_context_prefers_disambiguation():
    a = _Artist(
        1, "Nirvana", disambiguation="US grunge band", career_span="1987\u20131994", isgroup=1
    )
    assert artist_context_map([a]) == {1: "US grunge band"}


def test_artist_context_falls_back_to_career_span_then_group_person():
    span = _Artist(2, "Air", career_span="1995\u2013present", isgroup=1)
    group = _Artist(3, "Boston", isgroup=1)
    person = _Artist(4, "Prince", isgroup=0)
    bare = _Artist(5, "???", isgroup=None)
    out = artist_context_map([span, group, person, bare])
    assert out == {2: "1995\u2013present", 3: "Group", 4: "Person", 5: ""}


# ── place_context_map (AC 5) ─────────────────────────────────────────────


def test_place_context_type_and_country_from_parent_chain():
    country = _Place(10, "United Kingdom", "Country")
    region = _Place(11, "England", "Subdivision", parent=country)
    city = _Place(12, "Liverpool", "City", parent=region)
    assert place_context_map([city]) == {12: "City · United Kingdom"}


def test_place_context_degrades_to_type_or_country_or_empty():
    lonely = _Place(20, "Atlantis", "City")
    no_type = _Place(21, "Nowhere", None, parent=_Place(22, "Canada", "Country"))
    orphan = _Place(23, "Mystery", None)
    out = place_context_map([lonely, no_type, orphan])
    assert out == {20: "City", 21: "Canada", 23: ""}


def test_place_context_survives_parent_cycle():
    a = _Place(30, "A", "City")
    b = _Place(31, "B", "City", parent=a)
    a.parent = b  # self-referential FK gone wrong -- must not hang
    # No country in the chain; the walk terminates on the visited-set guard
    # and degrades to just the type rather than spinning forever.
    assert place_context_map([a]) == {30: "City"}


def test_place_context_omits_country_equal_to_own_name():
    usa = _Place(40, "United States", "Country")
    usa.parent = _Place(41, "United States", "Country")
    assert place_context_map([usa]) == {40: "Country"}


# ── track_context_map (AC 6) ─────────────────────────────────────────────


def test_track_context_artist_and_album():
    t = _Track(1, primary_artists=[_ArtistName("Radiohead")], album_name="OK Computer")
    assert track_context_map([t]) == {1: "Radiohead · OK Computer"}


def test_track_context_degrades():
    only_artist = _Track(2, primary_artists=[_ArtistName("Aphex Twin")])
    only_album = _Track(3, album_name="Untitled")
    nothing = _Track(4)
    multi = _Track(
        5, primary_artists=[_ArtistName("Jay-Z"), _ArtistName("Kanye West")], album_name="WTT"
    )
    out = track_context_map([only_artist, only_album, nothing, multi])
    assert out == {2: "Aphex Twin", 3: "Untitled", 4: "", 5: "Jay-Z, Kanye West · WTT"}


# ── album_context_map ────────────────────────────────────────────────────


def test_album_context_artist_and_year():
    a = _Album(1, album_artists=[_ArtistName("Radiohead")], release_year=1997)
    assert album_context_map([a]) == {1: "Radiohead · 1997"}


def test_album_context_degrades():
    only_artist = _Album(2, album_artists=[_ArtistName("Aphex Twin")])
    only_year = _Album(3, release_year=2001)
    nothing = _Album(4)
    multi = _Album(
        5, album_artists=[_ArtistName("Jay-Z"), _ArtistName("Kanye West")], release_year=2011
    )
    out = album_context_map([only_artist, only_year, nothing, multi])
    assert out == {2: "Aphex Twin", 3: "2001", 4: "", 5: "Jay-Z, Kanye West · 2011"}


# ── publisher_context_map ────────────────────────────────────────────────


def test_publisher_context_parent_and_lifespan():
    parent = _Publisher(1, "Universal Music Group")
    sub = _Publisher(2, "Interscope", parent=parent, begin_year=1990)
    assert publisher_context_map([sub]) == {2: "Universal Music Group · 1990\u2013present"}


def test_publisher_context_empty_when_nothing_known():
    assert publisher_context_map([_Publisher(3, "Indie")]) == {3: ""}
