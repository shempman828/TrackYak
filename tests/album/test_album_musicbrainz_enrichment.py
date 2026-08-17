"""Regression test: MusicBrainz album import only filled in blank dates and
never overwrote an incorrect existing release date.

Root cause: _apply_musicbrainz_enrichment() in album_musicbrainz_mixin.py
applied the same fill-blank-only guard to every scalar enrichment field,
including release_year/release_month/release_day (nullable QLineEdit
fields) -- so once an album already had a (possibly wrong) date, MB's
confirmed date could never replace it. Fixed by writing those fields
unconditionally, since by the time this runs the user has already reviewed
and accepted the MB release match.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QLineEdit

from src.album.album_musicbrainz_mixin import AlbumMusicBrainzMixin
from src.common.nullable_numeric_field import (
    create_nullable_int_field,
    nullable_field_value,
)


class _Host(AlbumMusicBrainzMixin):
    def __init__(self, field_widgets, album):
        self.field_widgets = field_widgets
        self.album = album


def test_apply_musicbrainz_enrichment_overwrites_existing_date(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=1999)
    month_widget = create_nullable_int_field(min_val=1, max_val=12, current_value=6)
    day_widget = create_nullable_int_field(min_val=1, max_val=31, current_value=15)

    host = _Host(
        field_widgets={
            "release_year": year_widget,
            "release_month": month_widget,
            "release_day": day_widget,
        },
        album=SimpleNamespace(),
    )

    host._apply_musicbrainz_enrichment(
        {"release_year": 2005, "release_month": 3, "release_day": 21}
    )

    assert nullable_field_value(year_widget) == 2005
    assert nullable_field_value(month_widget) == 3
    assert nullable_field_value(day_widget) == 21


def test_apply_musicbrainz_enrichment_still_fills_blank_date(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=None)

    host = _Host(field_widgets={"release_year": year_widget}, album=SimpleNamespace())

    host._apply_musicbrainz_enrichment({"release_year": 2005})

    assert nullable_field_value(year_widget) == 2005


def test_apply_musicbrainz_enrichment_still_fill_blank_only_for_other_fields(qapp):
    """catalog_number and similar QLineEdit fields weren't reported buggy --
    they should keep their original fill-blank-only behavior."""
    catalog_widget = QLineEdit("EXISTING-CATALOG-123")

    host = _Host(
        field_widgets={"catalog_number": catalog_widget}, album=SimpleNamespace()
    )

    host._apply_musicbrainz_enrichment({"catalog_number": "MB-CATALOG-999"})

    assert catalog_widget.text() == "EXISTING-CATALOG-123"
