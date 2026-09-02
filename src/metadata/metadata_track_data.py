"""Assembles everything the ID3/Vorbis tag builders need about a track
into one plain dict, so those builders don't need any database access of
their own - every database read for a metadata write happens here, once.
"""

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


class TrackDataAssembler:
    """Pulls a track's full metadata - the track itself, its album, disc,
    artists, genres, moods, publishers, places, playlist names, and
    sibling track/disc counts - into one dict via the DB controller."""

    def __init__(self, controller):
        self.controller = controller

    def get_track_data(self, track_id: int) -> dict[str, Any]:
        """Get complete track data from database using controller helpers."""
        try:
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track:
                return {}

            album = None
            if track.album_id:
                album = self.controller.get.get_entity_object("Album", album_id=track.album_id)

            disc = None
            if track.disc_id:
                disc = self.controller.get.get_entity_object("Disc", disc_id=track.disc_id)

            track_artist_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", track_id=track_id
            )
            artists_with_roles = []
            for tar in track_artist_roles:
                artist = self.controller.get.get_entity_object("Artist", artist_id=tar.artist_id)
                role = self.controller.get.get_entity_object("Role", role_id=tar.role_id)
                if artist and role:
                    artists_with_roles.append(
                        {
                            "artist": artist,
                            "role": role,
                            "credited_name": tar.credited_name,
                            "artist_mbid": artist.MBID,
                        }
                    )

            album_artists_with_roles = []
            if album:
                album_roles = self.controller.get.get_all_entities(
                    "AlbumRoleAssociation", album_id=album.album_id
                )
                for ar in album_roles:
                    artist = self.controller.get.get_entity_object("Artist", artist_id=ar.artist_id)
                    role = self.controller.get.get_entity_object("Role", role_id=ar.role_id)
                    if artist and role:
                        album_artists_with_roles.append(
                            {
                                "artist": artist,
                                "role": role,
                                "credited_name": ar.credited_name,
                                "artist_mbid": artist.MBID,
                            }
                        )

            track_genres = self.controller.get.get_all_entities("TrackGenre", track_id=track_id)
            genres = []
            for tg in track_genres:
                genre = self.controller.get.get_entity_object("Genre", genre_id=tg.genre_id)
                if genre:
                    genres.append(genre)

            mood_tracks = self.controller.get.get_all_entities(
                "MoodTrackAssociation", track_id=track_id
            )
            moods = []
            for mt in mood_tracks:
                mood = self.controller.get.get_entity_object("Mood", mood_id=mt.mood_id)
                if mood:
                    moods.append(mood)

            publishers = []
            if album:
                album_publishers = self.controller.get.get_all_entities(
                    "AlbumPublisher", album_id=album.album_id
                )
                for ap in album_publishers:
                    publisher = self.controller.get.get_entity_object(
                        "Publisher", publisher_id=ap.publisher_id
                    )
                    if publisher and publisher.publisher_name:
                        publishers.append(publisher.publisher_name)

            place_associations = self.controller.get.get_all_entities(
                "PlaceAssociation", entity_id=track_id, entity_type="Track"
            )
            places = []
            for pa in place_associations:
                place = self.controller.get.get_entity_object("Place", place_id=pa.place_id)
                if place:
                    places.append(place)

            disc_track_count = None
            if disc:
                sibling_tracks = self.controller.get.get_all_entities("Track", disc_id=disc.disc_id)
                if sibling_tracks:
                    disc_track_count = len(sibling_tracks)

            album_disc_count = None
            if album:
                sibling_discs = self.controller.get.get_all_entities(
                    "Disc", album_id=album.album_id
                )
                if sibling_discs and len(sibling_discs) > 1:
                    album_disc_count = len(sibling_discs)

            return {
                "track": track,
                "album": album,
                "disc": disc,
                "artists_with_roles": artists_with_roles,
                "album_artists_with_roles": album_artists_with_roles,
                "genres": genres,
                "moods": moods,
                "publishers": publishers,
                "places": places,
                "playlist_names": self._get_playlist_names_for_track(track_id),
                "disc_track_count": disc_track_count,
                "album_disc_count": album_disc_count,
            }
        except SQLAlchemyError as e:
            logger.debug(f"Error getting track data for ID {track_id}: {e}")
            return {}

    def _get_playlist_names_for_track(self, track_id: int) -> list:
        """Return a sorted list of playlist names this track belongs to.

        Excludes smart playlists — those are generated dynamically and
        don't need to be stored in file tags.

        Args:
            track_id: The database ID of the track.

        Returns:
            A list of playlist name strings, e.g. ["My Favourites", "Workout Mix"].
            Returns an empty list if the track is in no playlists or on error.
        """
        try:
            playlist_track_rows = self.controller.get.get_all_entities(
                "PlaylistTracks", track_id=track_id
            )
            if not playlist_track_rows:
                return []

            names = []
            for pt in playlist_track_rows:
                playlist = self.controller.get.get_entity_object(
                    "Playlist", playlist_id=pt.playlist_id
                )
                # Skip smart playlists — they regenerate themselves
                if playlist and playlist.playlist_name and not playlist.is_smart:
                    names.append(playlist.playlist_name)

            return sorted(set(names))  # Deduplicate and sort for consistency

        except SQLAlchemyError as e:
            logger.debug(f"Error fetching playlist names for track {track_id}: {e}")
            return []
