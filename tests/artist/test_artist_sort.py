"""AC1 -- artist_sort_key / artist_filing_name fallback rules.

See docs/specs/artist_sort_name_ordering.md.
"""

from src.artist.artist_sort import artist_filing_name, artist_sort_key


class StubArtist:
    def __init__(self, artist_name=None, sort_name=None):
        if artist_name is not None:
            self.artist_name = artist_name
        if sort_name is not None:
            self.sort_name = sort_name


def test_uses_sort_name_when_set():
    a = StubArtist(artist_name="The Beatles", sort_name="Beatles, The")
    assert artist_filing_name(a) == "Beatles, The"
    assert artist_sort_key(a) == "beatles, the"


def test_falls_back_to_name_when_sort_name_none():
    a = StubArtist(artist_name="Bee Gees", sort_name=None)
    assert artist_filing_name(a) == "Bee Gees"
    assert artist_sort_key(a) == "bee gees"


def test_falls_back_to_name_when_sort_name_blank():
    a = StubArtist(artist_name="Tom Waits", sort_name="   ")
    assert artist_filing_name(a) == "Tom Waits"
    assert artist_sort_key(a) == "tom waits"


def test_empty_string_when_both_missing():
    assert artist_filing_name(StubArtist()) == ""
    assert artist_sort_key(StubArtist()) == ""
    assert artist_filing_name(StubArtist(artist_name=None, sort_name=None)) == ""


def test_missing_sort_name_attribute_entirely():
    """Objects that never define sort_name (stubs, partial rows) must not raise."""
    a = StubArtist(artist_name="Neil Young")
    assert not hasattr(a, "sort_name")
    assert artist_sort_key(a) == "neil young"
