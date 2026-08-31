"""AC5 -- the orphan-artist delete dialog lists candidates in filing-name
order (Artist.sort_name) while the checkbox labels still show artist_name.

See docs/specs/artist_sort_name_ordering.md.
"""

from src.artist.artist_delete_orphans import OrphanArtistDialog


class StubArtist:
    def __init__(self, artist_id, artist_name, sort_name=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = sort_name


ORPHANS = [
    StubArtist(1, "Tom Waits", "Waits, Tom"),
    StubArtist(2, "The Beatles", "Beatles, The"),
    StubArtist(3, "Bee Gees", "Bee Gees"),
    StubArtist(4, "ABBA", None),  # no sort_name -> falls back to "abba"
]


def test_orphans_sorted_by_filing_name(qapp):
    dlg = OrphanArtistDialog(list(ORPHANS))
    assert [a.artist_name for a in dlg.orphans] == ["ABBA", "The Beatles", "Bee Gees", "Tom Waits"]


def test_checkbox_labels_show_plain_name(qapp):
    dlg = OrphanArtistDialog(list(ORPHANS))
    labels = [cb.text() for cb in dlg._checkboxes]
    assert labels == ["ABBA", "The Beatles", "Bee Gees", "Tom Waits"]
    assert "Beatles, The" not in labels
