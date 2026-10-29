"""AlbumEditor and NewAlbumDialog: construction, close handling, and the
Details/Tracks/Artwork/Advanced tabs.
"""

from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit
from sqlalchemy.orm.exc import DetachedInstanceError

from src.album.disc.disc_view import DiscTabView
from src.album.edit.album_new import NewAlbumDialog
from src.album.edit.base_album_edit import AlbumEditor
from src.album.edit.base_album_edit_tabs import AdvancedTab, ArtworkTab, DetailsTab, TracksTab
from src.db.db_mapping_albums import ALBUM_FIELDS

# ---------------------------------------------------------------------------
# Tests for NewAlbumDialog.
# ---------------------------------------------------------------------------


def test_new_album_dialog_constructs_without_raising(qapp):
    dialog = NewAlbumDialog(controller=None)

    assert dialog.album_name == ""
    assert dialog.release_year is None
    assert dialog.artist_name == ""
    assert dialog.is_compilation == 0


# ---------------------------------------------------------------------------
# Regression test for AlbumEditor.reject (Cancel / Esc / window close).
# ---------------------------------------------------------------------------


def test_reject_closes_when_album_is_detached(qapp):
    class _Bare(AlbumEditor):
        def __init__(self):
            QDialog.__init__(self)

    dialog = _Bare()

    with patch.object(AlbumEditor, "_has_unsaved_changes", side_effect=DetachedInstanceError("x")), patch.object(QDialog, "reject") as base_reject:
        dialog.reject()

    base_reject.assert_called_once()


# ---------------------------------------------------------------------------
# Regression tests for ArtworkTab and AdvancedTab in base_album_edit_tabs.py.
# ---------------------------------------------------------------------------


class _StubEditor:
    def __init__(self, album=None, field_widgets=None, cover_embed_worker=None):
        self.album = album
        self.field_widgets = field_widgets or {}
        self._cover_embed_worker = cover_embed_worker
        self.set_enabled_calls = []

    def _pick_cover(self, cover_type):
        pass

    def _clear_cover(self, cover_type):
        pass

    def _load_artwork_previews(self):
        pass

    def _set_cover_controls_enabled(self, enabled):
        self.set_enabled_calls.append(enabled)


def _stat_tiles(tab):
    """Map each stat tile's caption (e.g. "ALBUM GAIN (DB)") to its value text."""
    tiles = {}
    for value_lbl in tab.findChildren(QLabel, "StatTileValue"):
        tile = value_lbl.parentWidget()
        caption_lbl = tile.findChild(QLabel, "StatTileLabel") if tile else None
        if caption_lbl is not None:
            tiles[caption_lbl.text()] = value_lbl.text()
    return tiles


def test_artwork_tab_keeps_controls_disabled_when_embed_worker_running(qapp):
    editor = _StubEditor(cover_embed_worker=object())

    ArtworkTab(editor).build()

    assert editor.set_enabled_calls == [False]


def test_artwork_tab_leaves_controls_enabled_when_no_embed_running(qapp):
    editor = _StubEditor(cover_embed_worker=None)

    ArtworkTab(editor).build()

    assert editor.set_enabled_calls == []


def test_advanced_tab_renders_dash_for_corrupt_gain_and_peak(qapp):
    album = SimpleNamespace(album_gain="not-a-number", album_peak=object(), tracks=[], total_duration=None, total_plays=None, average_rating=None)
    editor = _StubEditor(album=album, field_widgets={})

    tab = AdvancedTab(editor).build()

    tiles = _stat_tiles(tab)
    # Non-numeric gain/peak fall back to their str() rather than crashing.
    assert tiles["ALBUM GAIN (DB)"] == "not-a-number"
    assert "ALBUM PEAK" in tiles


def test_advanced_tab_formats_numeric_gain_and_peak(qapp):
    album = SimpleNamespace(album_gain=-6.12345, album_peak=0.987654, tracks=[], total_duration=None, total_plays=None, average_rating=None)
    editor = _StubEditor(album=album, field_widgets={})

    tab = AdvancedTab(editor).build()

    texts = [w.text() for w in tab.findChildren(QLabel)]
    assert "-6.12" in texts
    assert "0.9877" in texts


def test_advanced_tab_first_pass_widget_still_added(qapp):
    """The removed dead _row() helper must not affect the first_pass/second_pass
    widgets, which are added directly (not via _row)."""
    field = QLineEdit()
    album = SimpleNamespace(album_gain=None, album_peak=None, tracks=[], total_duration=None, total_plays=None, average_rating=None)
    editor = _StubEditor(album=album, field_widgets={"first_pass": field})

    tab = AdvancedTab(editor).build()

    assert field in tab.findChildren(QLineEdit)


# ---------------------------------------------------------------------------
# Tests for bug #244-followup: removing a track from an album (via the
# ---------------------------------------------------------------------------


class _StubAlbum:
    def __init__(self, album_id=1, album_name="Test Album"):
        self.album_id = album_id
        self.album_name = album_name


class _StubGet:
    """DiscTabView.load_data() asks for Track/AlbumVirtualTrack/Disc
    rows scoped to the album; an empty library is enough to exercise the
    refresh/signal wiring without a real DB."""

    def get_all_entities(self, model_name, **kwargs):
        return []


class _StubController:
    def __init__(self):
        self.get = _StubGet()


class _RefreshEditor:
    def __init__(self):
        self.album = _StubAlbum()
        self.controller = _StubController()
        self.refresh_view_calls = 0

    def refresh_view(self):
        self.refresh_view_calls += 1


def test_refresh_view_emits_tracks_changed(qapp):
    album = _StubAlbum()
    controller = _StubController()
    view = DiscTabView(album, controller)

    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.refresh_view()

    assert emitted == [True]


def test_tracks_tab_forwards_track_changes_to_editor_refresh(qapp):
    editor = _RefreshEditor()
    tab = TracksTab(editor)

    built = tab.build()
    try:
        disc_view = built.findChild(DiscTabView)
        assert disc_view is not None

        disc_view.refresh_view()

        assert editor.refresh_view_calls == 1
    finally:
        built.deleteLater()


# ---------------------------------------------------------------------------
# Tests for the album editor surfacing Album.media_format, per
# ---------------------------------------------------------------------------


class _FieldsEditor:
    def __init__(self, field_widgets):
        self.field_widgets = field_widgets


def _row_labels_in_order(tab):
    """The row labels of a built DetailsTab's field-group forms, top to bottom."""
    labels = []
    for form in tab.findChildren(QFormLayout):
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.LabelRole)
            if item and isinstance(item.widget(), QLabel):
                labels.append(item.widget().text())
    return labels


def test_details_tab_shows_editable_media_format_under_release_country(qapp):
    media_widget = QLineEdit()
    widgets = {name: QLineEdit() for name in ALBUM_FIELDS}
    widgets["media_format"] = media_widget
    tab = DetailsTab(_FieldsEditor(widgets)).build()

    labels = _row_labels_in_order(tab)
    assert labels.index("Media Format:") == labels.index("Release Country:") + 1
    assert media_widget in tab.findChildren(QLineEdit)
    spec = ALBUM_FIELDS["media_format"]
    assert spec.editable is True and spec.type is str
