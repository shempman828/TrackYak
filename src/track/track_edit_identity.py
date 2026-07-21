# ---------------------------------------------------------------------------
# IdentificationTab — like FieldFormTab("Identification") but adds Wikipedia
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

from src.musicbrainz.musicbrainz_client import (
    complete_recording_enrichment,
    search_recordings,
)
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzMatchDialog
from src.track.track_edit_fieldform import FieldFormTab


class IdentificationTab(FieldFormTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__("Identification", tracks, controller, parent)
        # A single MusicBrainz recording match doesn't apply to a batch of
        # different tracks, so the lookup is single-track only.
        if not self.is_multi:
            self._add_lookup_button()

    def _add_lookup_button(self):
        self.lookup_button = QPushButton("🎵 Look Up on MusicBrainz")
        self.lookup_button.setToolTip(
            "Search MusicBrainz recordings by track/artist/album and fill in "
            "blank fields (MBID, ISRC) from the selected match. Never "
            "overwrites fields you've already filled in."
        )
        self.lookup_button.clicked.connect(self._lookup_musicbrainz)
        self.layout().addRow(self.lookup_button)

    def _lookup_musicbrainz(self):
        track_name = (self.track.track_name or "").strip()
        if not track_name:
            QMessageBox.warning(
                self, "MusicBrainz Lookup", "This track has no title to look up."
            )
            return

        artist_name = self.track.primary_artist_names
        if artist_name == "Unknown Artist":
            artist_name = None
        album_name = self.track.album_name or None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"track '{track_name}'",
            search_call=lambda: search_recordings(track_name, artist_name, album_name),
            complete_call=complete_recording_enrichment,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        enrichment = dialog.result_enrichment()
        if enrichment:
            self.set_if_empty(enrichment)
