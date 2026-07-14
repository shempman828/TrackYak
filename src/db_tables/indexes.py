"""
Explicit indexes on top of the ORM models, grouped by owning table.

Importing this module registers the indexes with each table's metadata as a
side effect, so it must be imported after all model modules.
"""

from sqlalchemy import Index

from src.db_tables.album import Album
from src.db_tables.artist import Artist
from src.db_tables.associations import (
    AlbumPublisher,
    AlbumRoleAssociation,
    TrackArtistRole,
    TrackGenre,
)
from src.db_tables.award import AwardAssociation
from src.db_tables.disc import Disc
from src.db_tables.genre import Genre
from src.db_tables.mood import MoodTrackAssociation
from src.db_tables.place import PlaceAssociation
from src.db_tables.track import Track, TrackUsage

# --- Artist ---
Index("idx_artists_name", Artist.artist_name)
Index("idx_artist_begin_end", Artist.begin_year, Artist.end_year)

# --- Album ---
Index("idx_albums_title", Album.album_name)
Index("idx_albums_release_year", Album.release_year)  # Commonly filtered/sorted

# --- Track ---
Index("idx_tracks_path", Track.track_file_path)
Index("idx_tracks_title", Track.track_name)
Index("idx_track_album_id", Track.album_id)
Index("idx_track_disc_id", Track.disc_id)
Index("idx_tracks_disc_id", Track.disc_id)
Index("idx_tracks_track_number", Track.track_number)  # Used in sort and gap detection
Index("idx_tracks_play_count", Track.play_count)  # Useful for "most played" queries
Index("idx_tracks_user_rating", Track.user_rating)  # Useful for "top rated" queries

# --- Genre ---
Index("idx_genres_name", Genre.genre_name)

# --- Disc ---
Index("idx_discs_album_number", Disc.album_id, Disc.disc_number)

# --- Junction tables ---
Index("idx_track_artist_roles", TrackArtistRole.artist_id, TrackArtistRole.track_id)
Index("idx_album_roles", AlbumRoleAssociation.album_id, AlbumRoleAssociation.artist_id)
Index(
    "idx_album_roles_artist", AlbumRoleAssociation.artist_id
)  # Reverse lookup: artist → albums
Index("idx_track_genres", TrackGenre.track_id, TrackGenre.genre_id)
Index(
    "idx_mood_track_association",
    MoodTrackAssociation.mood_id,
    MoodTrackAssociation.track_id,
)

# --- Publisher ---
Index("ix_album_publisher_unique", "album_id", "publisher_id", unique=True)
Index("idx_album_publisher_publisher_id", AlbumPublisher.publisher_id)

# --- Place associations ---
Index("idx_place_associations", PlaceAssociation.place_id, PlaceAssociation.entity_id)
Index(
    "idx_place_assoc_entity_type", PlaceAssociation.entity_type
)  # Filter by type quickly

# --- Award associations ---
Index("idx_award_associations", AwardAssociation.award_id, AwardAssociation.entity_id)
Index(
    "idx_award_assoc_entity_type", AwardAssociation.entity_type
)  # Filter by type quickly

# --- TrackUsage ---
Index("idx_track_usages_track_id", TrackUsage.track_id)
Index("idx_track_usages_type", TrackUsage.usage_type)
