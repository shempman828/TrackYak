from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QMessageBox, QSpinBox

from src.common.nullable_spinbox import NullableSpinBox
from src.musicbrainz.musicbrainz_client import search_release_groups
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzMatchDialog


class AlbumMusicBrainzMixin:
    """
    MusicBrainz release-group lookup and enrichment for AlbumEditor.

    Expects the host class to provide: self.album, self.field_widgets, and
    to be a QWidget subclass.
    """

    def _lookup_musicbrainz(self):
        title_widget = self.field_widgets.get("album_name")
        album_name = (
            title_widget.text().strip()
            if isinstance(title_widget, QLineEdit)
            else (self.album.album_name or "")
        ).strip()
        if not album_name:
            QMessageBox.warning(
                self, "MusicBrainz Lookup", "Enter an album title before looking it up."
            )
            return

        artist_names = getattr(self.album, "album_artist_names", None)
        if artist_names in (None, "Unknown Artist"):
            artist_names = None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"album '{album_name}'",
            search_call=lambda: search_release_groups(album_name, artist_names),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        enrichment = dialog.result_enrichment()
        if enrichment:
            self._apply_musicbrainz_enrichment(enrichment)

    def _apply_musicbrainz_enrichment(self, enrichment: dict):
        """Fill field widgets from a MusicBrainz enrichment dict, but only
        where the widget is still at its blank/default state -- never
        overwrites something the user already filled in or typed moments ago.

        NullableSpinBox fields (release_year/month/day, estimated_sales) are
        "blank" when the spin box sits on its empty sentinel -- value()
        returns None.

        QCheckBox fields (is_live/is_compilation) have no blank state at
        all, so they fall back to the originally-loaded album's value being
        None, combined with the widget still being unchecked -- applied
        only when both hold, so a deliberate manual uncheck just before the
        lookup is never clobbered.
        """
        for field_name, value in enrichment.items():
            widget = self.field_widgets.get(field_name)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                if not widget.text().strip():
                    widget.setText(str(value))
            elif isinstance(widget, NullableSpinBox):
                if widget.value() is None:
                    widget.setValue(int(value))
            elif isinstance(widget, QSpinBox):
                if widget.value() == widget.minimum():
                    widget.setValue(int(value))
            elif isinstance(widget, QCheckBox):
                if (
                    getattr(self.album, field_name, None) is None
                    and not widget.isChecked()
                ):
                    widget.setChecked(bool(value))
