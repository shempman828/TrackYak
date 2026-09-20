"""Reconstructs playlist membership for an imported track from its PLAYLIST metadata tags."""

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


class PlaylistTagImporter:
    """Reads PLAYLIST tags from metadata and adds the track to the named playlists."""

    def __init__(self, controller):
        self.controller = controller

    def _process_playlist_tags(self, track, metadata: dict):
        """Read PLAYLIST tags from metadata and reconstruct playlist membership for the track."""
        try:
            # ── 1. Collect playlist name(s) from metadata ──────────────
            playlist_names = []

            # Vorbis (FLAC/OGG): stored as PLAYLIST (may be a list if multiple playlists)
            vorbis_playlists = metadata.get("PLAYLIST") or metadata.get("playlist")
            if vorbis_playlists:
                if isinstance(vorbis_playlists, list):
                    playlist_names.extend(vorbis_playlists)
                else:
                    playlist_names.append(str(vorbis_playlists))

            # ID3: stored as TXXX:PLAYLIST, multiple values joined by " ; "
            id3_playlists = metadata.get("TXXX:PLAYLIST") or metadata.get("txxx:playlist")
            if id3_playlists:
                # id3_playlists may itself be a list (if somehow multiple
                # TXXX:PLAYLIST frames exist)
                if isinstance(id3_playlists, list):
                    for entry in id3_playlists:
                        # Each entry may contain " ; "-separated names
                        playlist_names.extend(
                            [p.strip() for p in str(entry).split(" ; ") if p.strip()]
                        )
                else:
                    playlist_names.extend(
                        [p.strip() for p in str(id3_playlists).split(" ; ") if p.strip()]
                    )

            # Remove duplicates and blanks
            playlist_names = list(dict.fromkeys(name for name in playlist_names if name))

            if not playlist_names:
                return  # No playlist tags found — nothing to do

            logger.info(f"Track '{track.track_name}' has playlist tags: {playlist_names}")

            # ── 2. For each playlist name, find-or-create the playlist ──
            for playlist_name in playlist_names:
                self._add_track_to_playlist_by_name(track, playlist_name)

        except SQLAlchemyError as e:
            logger.error(
                f"Error processing playlist tags for track {track.track_id}: {e}", exc_info=True
            )

    def _add_track_to_playlist_by_name(self, track, playlist_name: str):
        """Find or create a playlist by exact name, then append the track to it."""
        try:
            # ── Find or create the playlist ────────────────────────────
            playlist = self.controller.get.get_entity_object(
                "Playlist", playlist_name=playlist_name
            )

            if not playlist:
                logger.info(f"Creating new playlist from tag: '{playlist_name}'")
                playlist = self.controller.add.add_entity(
                    "Playlist", commit=False, playlist_name=playlist_name, is_smart=0
                )
                if not playlist:
                    logger.error(f"Failed to create playlist '{playlist_name}'")
                    return

            # ── Check the track isn't already in this playlist ─────────
            existing = self.controller.get.get_entity_object(
                "PlaylistTracks", playlist_id=playlist.playlist_id, track_id=track.track_id
            )
            if existing:
                logger.debug(
                    f"Track '{track.track_name}' is already in playlist "
                    f"'{playlist_name}' — skipping"
                )
                return

            # ── Determine the next position ────────────────────────────
            # Get all current tracks in the playlist to find the highest position
            existing_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id=playlist.playlist_id
            )
            next_position = max((pt.position for pt in existing_tracks), default=0) + 1

            # ── Add the track ──────────────────────────────────────────
            self.controller.add.add_entity(
                "PlaylistTracks",
                commit=False,
                playlist_id=playlist.playlist_id,
                track_id=track.track_id,
                position=next_position,
                date_added=datetime.now(UTC),
            )

            logger.info(
                f"Added '{track.track_name}' to playlist '{playlist_name}' "
                f"at position {next_position}"
            )

        except SQLAlchemyError as e:
            logger.error(f"Error adding track to playlist '{playlist_name}': {e}", exc_info=True)
