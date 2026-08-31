"""AC4 -- the "Add Member" individual-artist combo is ordered by filing
name (Artist.sort_name); item text stays the plain artist_name.

See docs/specs/artist_sort_name_ordering.md.
"""

from PySide6.QtWidgets import QComboBox

from src.artist.artist_group_dialog import _populate_individual_artist_combo


class StubArtist:
    def __init__(self, artist_id, artist_name, sort_name=None, isgroup=False):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = sort_name
        self.isgroup = isgroup


class StubGetController:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, entity_type):
        return list(self._artists)


class StubController:
    def __init__(self, artists):
        self.get = StubGetController(artists)


def test_combo_ordered_by_filing_name(qapp):
    artists = [
        StubArtist(1, "Tom Waits", "Waits, Tom"),
        StubArtist(2, "The Beatles", "Beatles, The"),
        StubArtist(3, "Bee Gees", "Bee Gees"),
        StubArtist(4, "The Kinks", isgroup=True),  # filtered out (group)
    ]
    combo = QComboBox()
    _populate_individual_artist_combo(combo, StubController(artists))

    # index 0 is the "-- Select Artist --" placeholder
    items = [combo.itemText(i) for i in range(1, combo.count())]
    assert items == ["The Beatles", "Bee Gees", "Tom Waits"]
    assert "Waits, Tom" not in items
