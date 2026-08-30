"""Unit tests for the artist-detail ArtistInfobox.

Regression context: the type badge joined every artist type into one
non-wrapping QLabel with a hard setFixedHeight(24), sitting inside the
220px fixed-width infobox. An artist with several types overflowed the
label width and Qt clipped the text. The badge must now word-wrap and be
free to grow vertically so the full type list stays visible.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QLabel

from src.artist.artist_detail_header import ArtistInfobox

# Qt's "no maximum" sentinel for widget dimensions.
QWIDGETSIZE_MAX = (1 << 24) - 1


def _artist(type_names):
    return SimpleNamespace(
        types=[SimpleNamespace(type_name=n) for n in type_names],
        profile_pic_path="",
        begin_year=None,
        end_year=None,
        begin_month=None,
        end_month=None,
        begin_day=None,
        end_day=None,
    )


def _infobox(type_names):
    # Held by the caller: without a live reference Qt GCs the box and
    # deletes its child labels out from under the assertions.
    return ArtistInfobox(_artist(type_names))


def _type_badge(box):
    return box.findChild(QLabel, "TypeBadge")


def test_type_badge_shows_all_types(qapp):
    names = ["Person", "Group", "Orchestra", "Choir", "Character", "Other"]
    box = _infobox(names)
    for name in names:
        assert name in _type_badge(box).text()


def test_type_badge_wraps_and_is_not_height_capped(qapp):
    box = _infobox(["Person", "Group", "Orchestra"])
    badge = _type_badge(box)
    assert badge.wordWrap() is True
    # A hard setFixedHeight() pins both min and max to 24; the badge must
    # be free to grow past a single line.
    assert badge.maximumHeight() == QWIDGETSIZE_MAX


def test_type_badge_falls_back_to_artist_label(qapp):
    box = _infobox([])
    assert _type_badge(box).text() == "Artist"
