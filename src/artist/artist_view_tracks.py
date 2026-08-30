"""
artist_view_tracks.py

Track lookup for ArtistView: gathering every Track associated with an
artist via both track-level and album-level credits, and displaying them
in a BaseTrackView.
"""

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.track.base_track_view import BaseTrackView


class ArtistViewTracksMixin:
    """
    Expects the host class to provide: self.controller, and to be a
    QWidget subclass.
    """

    def _view_artist_tracks(self, artist):
        """Load all tracks associated with an artist and display them in a BaseTrackView."""
        try:
            tracks = self._get_all_artist_tracks(artist.artist_id)

            if not tracks:
                show_status_message(self, f"No tracks found for '{artist.artist_name}'.")
                return

            track_view = BaseTrackView(
                controller=self.controller,
                tracks=tracks,
                title=f"Tracks — {artist.artist_name} ({len(tracks)} tracks)",
                enable_drag=True,
                enable_drop=False,
            )
            track_view.exec_()

            logger.info(
                f"Displayed {len(tracks)} tracks for artist '{artist.artist_name}' "
                f"(id={artist.artist_id})"
            )

        except SQLAlchemyError as e:
            logger.error(
                f"Error displaying tracks for artist {artist.artist_id}: {e}", exc_info=True
            )
            QMessageBox.critical(
                self, "Error", f"Failed to load tracks for '{artist.artist_name}':\n{e}"
            )

    def _get_all_artist_tracks(self, artist_id: int) -> list:
        """
        Return a deduplicated list of Track objects associated with an artist
        via both TrackArtistRole (track-level credits) and AlbumRoleAssociation
        (album-level credits, e.g. album artist).
        """
        seen_ids = set()
        tracks = []

        try:
            track_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", artist_id=artist_id
            )
            for role in track_roles:
                track = self.controller.get.get_entity_object("Track", track_id=role.track_id)
                if track and track.track_id not in seen_ids:
                    seen_ids.add(track.track_id)
                    tracks.append(track)
        except SQLAlchemyError as e:
            logger.warning(f"Error fetching track-level credits: {e}")

        try:
            album_roles = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", artist_id=artist_id
            )
            for role in album_roles:
                album_tracks = self.controller.get.get_all_entities("Track", album_id=role.album_id)
                for track in album_tracks:
                    if track.track_id not in seen_ids:
                        seen_ids.add(track.track_id)
                        tracks.append(track)
        except SQLAlchemyError as e:
            logger.warning(f"Error fetching album-level credits: {e}")

        return tracks
