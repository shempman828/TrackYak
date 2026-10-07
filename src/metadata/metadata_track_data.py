"""Assembles everything the tag builders need about a track into one dict, so they need no database access."""

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


class TrackDataAssembler:
    """Reads a track and its album, disc, credits, genres, moods, publishers, places and playlists into one dict."""

    def __init__(self, controller):
        """Keep the database controller used for every read."""
        self.controller = controller

    def get_track_data(self, track_id: int) -> dict[str, Any]:
        """Return the complete track-data dict for track_id, or {} if the track is missing or a read fails."""
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

            # Track and album credits often repeat the same artists and roles, so look each up once.
            artist_cache: dict[int, Any] = {}
            role_cache: dict[int, Any] = {}

            def credits_with_roles(rows) -> list[dict[str, Any]]:
                """Resolve credit rows into artist/role dicts, skipping rows whose artist or role is missing."""
                credits = []
                for row in rows:
                    if row.artist_id not in artist_cache:
                        artist_cache[row.artist_id] = self.controller.get.get_entity_object("Artist", artist_id=row.artist_id)
                    if row.role_id not in role_cache:
                        role_cache[row.role_id] = self.controller.get.get_entity_object("Role", role_id=row.role_id)
                    artist, role = artist_cache[row.artist_id], role_cache[row.role_id]
                    if artist and role:
                        credits.append({"artist": artist, "role": role, "credited_name": row.credited_name, "artist_mbid": artist.MBID})
                return credits

            artists_with_roles = credits_with_roles(self.controller.get.get_all_entities("TrackArtistRole", track_id=track_id))
            album_artists_with_roles = credits_with_roles(self.controller.get.get_all_entities("AlbumRoleAssociation", album_id=album.album_id)) if album else []

            track_genres = self.controller.get.get_all_entities("TrackGenre", track_id=track_id)
            genres = []
            for tg in track_genres:
                genre = self.controller.get.get_entity_object("Genre", genre_id=tg.genre_id)
                if genre:
                    genres.append(genre)

            mood_tracks = self.controller.get.get_all_entities("MoodTrackAssociation", track_id=track_id)
            moods = []
            for mt in mood_tracks:
                mood = self.controller.get.get_entity_object("Mood", mood_id=mt.mood_id)
                if mood:
                    moods.append(mood)

            publishers = []
            if album:
                album_publishers = self.controller.get.get_all_entities("AlbumPublisher", album_id=album.album_id)
                for ap in album_publishers:
                    publisher = self.controller.get.get_entity_object("Publisher", publisher_id=ap.publisher_id)
                    if publisher and publisher.publisher_name:
                        publishers.append(publisher.publisher_name)

            place_associations = self.controller.get.get_all_entities("PlaceAssociation", entity_id=track_id, entity_type="Track")
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
                sibling_discs = self.controller.get.get_all_entities("Disc", album_id=album.album_id)
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
        """Return the sorted, de-duplicated names of the non-smart playlists that hold the track."""
        try:
            playlist_track_rows = self.controller.get.get_all_entities("PlaylistTracks", track_id=track_id)
            if not playlist_track_rows:
                return []

            names = []
            for pt in playlist_track_rows:
                playlist = self.controller.get.get_entity_object("Playlist", playlist_id=pt.playlist_id)
                # Smart playlists regenerate themselves, so they are not stored in tags.
                if playlist and playlist.playlist_name and not playlist.is_smart:
                    names.append(playlist.playlist_name)

            return sorted(set(names))

        except SQLAlchemyError as e:
            logger.debug(f"Error fetching playlist names for track {track_id}: {e}")
            return []
