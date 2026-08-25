"""Regression test: the MusicBrainz "date hint" used stale saved data.

Root cause: _lookup_musicbrainz() in album_musicbrainz_mixin.py fell back to
self.album.release_year whenever the release_year widget read as empty --
but self.album isn't updated until Save, so clearing the widget (e.g. to
discard a wrong 2015 remaster year before looking up the true 1961 release)
didn't disable the hint, it just resurrected the stale saved value. Fixed by
trusting only the live widget and dropping the self.album fallback entirely.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog, QLineEdit

from src.album.album_musicbrainz_mixin import AlbumMusicBrainzMixin
from src.common.nullable_numeric_field import (
    create_nullable_int_field,
    set_nullable_field_value,
)


class _Host(AlbumMusicBrainzMixin):
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

    with patch(
        "src.album.album_musicbrainz_mixin.MusicBrainzMatchDialog",
        side_effect=fake_dialog_ctor,
    ), patch(
        "src.album.album_musicbrainz_mixin.search_canonical_releases"
    ) as fake_search:
        host._lookup_musicbrainz()
        captured["search_call"]()

    return fake_search.call_args.kwargs["expected_year"]


def test_clearing_year_field_disables_hint_instead_of_using_stale_album_value(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=2015)
    set_nullable_field_value(year_widget, None)  # user clears it, unsaved

    host = _Host(
        field_widgets={
            "album_name": QLineEdit("King of the Tenors"),
            "release_year": year_widget,
        },
        # self.album still has the old, un-saved 2015 value -- must not be used.
        album=SimpleNamespace(release_year=2015, album_artist_names="Ben Webster"),
    )

    assert _run_lookup_and_capture_expected_year(host) is None


def test_live_year_field_value_is_still_used_as_hint(qapp):
    year_widget = create_nullable_int_field(min_val=1000, max_val=9999, current_value=1961)

    host = _Host(
        field_widgets={
            "album_name": QLineEdit("King of the Tenors"),
            "release_year": year_widget,
        },
        album=SimpleNamespace(release_year=1961, album_artist_names="Ben Webster"),
    )

    assert _run_lookup_and_capture_expected_year(host) == 1961
