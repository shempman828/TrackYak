"""Tests for the album editor surfacing Album.media_format, per
docs/specs/media-format-type.md AC11.

Covers:
  - DetailsTab.build() renders a "Media Format:" row directly under
    "Release Country:", bound to the media_format field widget.
  - media_format is a registered editable ALBUM_FIELD, so the editor's
    generic save/diff path (_collect_changed_fields) picks it up and a
    cleared widget writes NULL.
  - The completer suggestion list carries MusicBrainz's common carriers.
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit

from src.album.base_album_edit import MEDIA_FORMAT_SUGGESTIONS
from src.album.base_album_edit_tabs import DetailsTab
from src.db.db_mapping_albums import ALBUM_FIELDS


class _StubEditor:
    def __init__(self, field_widgets):
        self.field_widgets = field_widgets


def _row_labels_in_order(tab):
    """The left-column row labels of a built DetailsTab, top to bottom."""
    labels = []
    for child in tab.findChildren(QHBoxLayout):
        for i in range(child.count()):
            w = child.itemAt(i).widget()
            if isinstance(w, QLabel):
                labels.append(w.text())
    return labels


def test_details_tab_has_media_format_row_under_release_country(qapp):
    widgets = {name: QLineEdit() for name in ALBUM_FIELDS}
    tab = DetailsTab(_StubEditor(widgets)).build()

    labels = _row_labels_in_order(tab)
    assert "Media Format:" in labels
    assert labels.index("Media Format:") == labels.index("Release Country:") + 1


def test_details_tab_media_format_row_is_bound_to_the_field_widget(qapp):
    media_widget = QLineEdit()
    widgets = {name: QLineEdit() for name in ALBUM_FIELDS}
    widgets["media_format"] = media_widget
    tab = DetailsTab(_StubEditor(widgets)).build()

    assert media_widget.parent() is not None  # got placed into the layout
    assert media_widget in tab.findChildren(QLineEdit)


def test_media_format_is_an_editable_album_field():
    spec = ALBUM_FIELDS.get("media_format")
    assert spec is not None
    assert spec.editable is True
    assert spec.type is str  # generic string save/diff path applies


def test_media_format_suggestions_cover_common_carriers():
    assert "CD" in MEDIA_FORMAT_SUGGESTIONS
    assert '12" Vinyl' in MEDIA_FORMAT_SUGGESTIONS
    assert "Digital Media" in MEDIA_FORMAT_SUGGESTIONS
