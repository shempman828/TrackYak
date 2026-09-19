"""AC6 -- AlbumView's "Sort by Artist" keys off the first artist's filing
name (Artist.sort_name), with the plain-string / dict fallbacks intact.

See docs/specs/artist_sort_name_ordering.md.
"""

from src.album.album_sorting import AlbumSortingMixin


class _SortHost(AlbumSortingMixin):
    def __init__(self, albums):
        self.filtered_albums = list(albums)
        self._sort_criteria = "artist"
        self._sort_descending = False
        self._random_keys = {}

    def _get_track_count(self, album):
        return getattr(album, "track_count", 0)


class StubArtist:
    def __init__(self, artist_name, sort_name=None):
        self.artist_name = artist_name
        self.sort_name = sort_name


class StubAlbum:
    def __init__(self, album_name, album_artists):
        self.album_name = album_name
        self.album_artists = album_artists


def _order(albums):
    host = _SortHost(albums)
    host._sort_filtered()
    return [a.album_name for a in host.filtered_albums]


def test_orm_artist_sorted_by_filing_name():
    beatles = StubAlbum("Abbey Road", [StubArtist("The Beatles", "Beatles, The")])
    holly = StubAlbum("The Chirping Crickets", [StubArtist("Buddy Holly", "Holly, Buddy")])
    # "Beatles, The" < "Holly, Buddy" -> Abbey Road first, even though the
    # display names would put "Buddy Holly" before "The Beatles".
    assert _order([holly, beatles]) == ["Abbey Road", "The Chirping Crickets"]


def test_missing_sort_name_falls_back_to_display_name():
    a = StubAlbum("Aaa", [StubArtist("Zzz Top", None)])
    b = StubAlbum("Bbb", [StubArtist("Aardvark", None)])
    assert _order([a, b]) == ["Bbb", "Aaa"]


def test_string_and_dict_artist_rows_still_sort():
    s = StubAlbum("StrAlbum", ["The Beatles"])
    d = StubAlbum("DictAlbum", [{"artist_name": "ABBA"}])
    d_sort = StubAlbum("DictSortAlbum", [{"sort_name": "Aaa, A", "artist_name": "Zzz"}])
    # "aaa, a" < "abba" < "the beatles"
    assert _order([s, d, d_sort]) == ["DictSortAlbum", "DictAlbum", "StrAlbum"]


def test_no_artists_sorts_as_empty_string_without_crashing():
    empty = StubAlbum("EmptyAlbum", [])
    named = StubAlbum("NamedAlbum", [StubArtist("Beatles", "Beatles")])
    assert _order([named, empty]) == ["EmptyAlbum", "NamedAlbum"]


def test_numeric_criteria_fallback_is_numeric_not_string():
    # One album's _get_track_count raises; the fallback used to always be
    # "", which mixed a str key into a column of ints and raised TypeError
    # mid-sort. The fallback must match the criteria's own type instead.
    good_low = StubAlbum("Low", [])
    good_low.track_count = 1
    good_high = StubAlbum("High", [])
    good_high.track_count = 5

    class Bad:
        track_count = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    bad = Bad()
    bad.album_name = "Bad"

    host = _SortHost([good_high, bad, good_low])
    host._sort_criteria = "track_count"
    host._sort_filtered()
    # No TypeError raised, and the two well-formed albums still land in
    # numeric order around the failed one.
    names = [getattr(a, "album_name") for a in host.filtered_albums]
    assert names.index("Low") < names.index("High")
