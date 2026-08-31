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
