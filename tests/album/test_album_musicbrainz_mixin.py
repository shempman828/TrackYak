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
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog, QLineEdit

from src.album.album_musicbrainz_mixin import AlbumMusicBrainzMixin
from src.common.nullable_numeric_field import (
    create_nullable_int_field,
    nullable_field_value,
    set_nullable_field_value,
)


# ---- test_album_musicbrainz_enrichment.py ------------------------------------
class _Host_enr(AlbumMusicBrainzMixin):
    def __init__(self, field_widgets, album):
        self.field_widgets = field_widgets
        self.album = album


def test_apply_musicbrainz_enrichment_overwrites_existing_date(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=1999)
    month_widget = create_nullable_int_field(min_val=1, max_val=12, current_value=6)
    day_widget = create_nullable_int_field(min_val=1, max_val=31, current_value=15)

    host = _Host_enr(
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

    host = _Host_enr(field_widgets={"release_year": year_widget}, album=SimpleNamespace())

    host._apply_musicbrainz_enrichment({"release_year": 2005})

    assert nullable_field_value(year_widget) == 2005


def test_apply_musicbrainz_enrichment_still_fill_blank_only_for_other_fields(qapp):
    """catalog_number and similar QLineEdit fields weren't reported buggy --
    they should keep their original fill-blank-only behavior."""
    catalog_widget = QLineEdit("EXISTING-CATALOG-123")

    host = _Host_enr(field_widgets={"catalog_number": catalog_widget}, album=SimpleNamespace())

    host._apply_musicbrainz_enrichment({"catalog_number": "MB-CATALOG-999"})

    assert catalog_widget.text() == "EXISTING-CATALOG-123"


# ---- test_album_musicbrainz_year_hint.py -------------------------------------
# Regression test: the MusicBrainz "date hint" used stale saved data.
#
# Root cause: _lookup_musicbrainz() in album_musicbrainz_mixin.py fell back to
# self.album.release_year whenever the release_year widget read as empty --
# but self.album isn't updated until Save, so clearing the widget (e.g. to
# discard a wrong 2015 remaster year before looking up the true 1961 release)
# didn't disable the hint, it just resurrected the stale saved value. Fixed by
# trusting only the live widget and dropping the self.album fallback entirely.
class _Host_yh(AlbumMusicBrainzMixin):
    def __init__(self, field_widgets, album):
        self.field_widgets = field_widgets
        self.album = album


def _run_lookup_and_capture_expected_year(host):
    captured = {}

    def fake_dialog_ctor(entity_label, search_call, parent=None, **kwargs):
        captured["search_call"] = search_call
        dlg = MagicMock()
        dlg.exec.return_value = QDialog.Rejected
        return dlg

    with (
        patch(
            "src.album.album_musicbrainz_mixin.MusicBrainzMatchDialog", side_effect=fake_dialog_ctor
        ),
        patch("src.album.album_musicbrainz_mixin.search_canonical_releases") as fake_search,
    ):
        host._lookup_musicbrainz()
        captured["search_call"]()

    return fake_search.call_args.kwargs["expected_year"]


def test_clearing_year_field_disables_hint_instead_of_using_stale_album_value(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=2015)
    set_nullable_field_value(year_widget, None)  # user clears it, unsaved

    host = _Host_yh(
        field_widgets={"album_name": QLineEdit("King of the Tenors"), "release_year": year_widget},
        # self.album still has the old, un-saved 2015 value -- must not be used.
        album=SimpleNamespace(release_year=2015, album_artist_names="Ben Webster"),
    )

    assert _run_lookup_and_capture_expected_year(host) is None


def test_live_year_field_value_is_still_used_as_hint(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=1961)

    host = _Host_yh(
        field_widgets={"album_name": QLineEdit("King of the Tenors"), "release_year": year_widget},
        album=SimpleNamespace(release_year=1961, album_artist_names="Ben Webster"),
    )

    assert _run_lookup_and_capture_expected_year(host) == 1961


# ---- test_album_musicbrainz_media_format.py --------------------------------
# media_format is a per-pressing scalar imported from MusicBrainz, fill-blank
# only (same rule as catalog_number / release_country) -- it must never
# overwrite a carrier the user already set.
from src.album.album_musicbrainz_mixin import _SCALAR_ENRICHMENT_FIELDS  # noqa: E402


def test_media_format_is_a_fill_blank_scalar_enrichment_field():
    assert "media_format" in _SCALAR_ENRICHMENT_FIELDS


def test_apply_musicbrainz_enrichment_fills_blank_media_format(qapp):
    media_format_widget = QLineEdit("")

    host = _Host_enr(field_widgets={"media_format": media_format_widget}, album=SimpleNamespace())

    host._apply_musicbrainz_enrichment({"media_format": "CD"})

    assert media_format_widget.text() == "CD"


def test_apply_musicbrainz_enrichment_does_not_overwrite_existing_media_format(qapp):
    media_format_widget = QLineEdit("Vinyl")

    host = _Host_enr(field_widgets={"media_format": media_format_widget}, album=SimpleNamespace())

    host._apply_musicbrainz_enrichment({"media_format": "CD"})

    assert media_format_widget.text() == "Vinyl"
