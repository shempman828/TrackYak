"""
Explicit indexes on top of the ORM models, grouped by owning table.

Importing this module registers the indexes with each table's metadata as a
side effect, so it must be imported after all model modules.

Indexes deliberately omitted here because they'd be redundant with an
existing PRIMARY KEY / UNIQUE constraint (SQLite auto-indexes both):
  - Track.track_file_path — already unique=True
  - TrackGenre(track_id, genre_id), ArtistTypeAssociation(artist_id, artist_type_id),
    MoodTrackAssociation(mood_id, track_id) — already composite primary keys
  - AlbumPublisher(album_id, publisher_id) — already a composite primary key
  - AlbumRoleAssociation(album_id, artist_id) — already a leftmost prefix of
    the uq_album_artist_role(album_id, artist_id, role_id) unique constraint
  - ChartEntry(chart_id, chart_week) — already a leftmost prefix of the
    uq_chart_entry_week_position(chart_id, chart_week, position) unique
    constraint, which covers the week-browser's primary query

Note: SQLAlchemy's create_all() only emits DDL (including indexes) for
tables that don't already exist — adding an Index() here does nothing for
a database file where the owning table was created before this line was
added. MusicDatabase._create_missing_indexes() in database.py handles
backfilling indexes onto already-existing tables at startup.
"""

from sqlalchemy import Index

from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist, ArtistInfluence, GroupMembership
from src.db.db_tables.associations import (
    AlbumPublisher,
    AlbumRoleAssociation,
    ArtistTypeAssociation,
    PublisherFounder,
    TrackArtistRole,
    TrackGenre,
)
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.chart import ChartEntry
from src.db.db_tables.disc import Disc
from src.db.db_tables.genre import Genre
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.playlist import PlaylistTracks, SmartPlaylistCriteria
from src.db.db_tables.publisher import Publisher
from src.db.db_tables.role import Role
from src.db.db_tables.track import Samples, Track, TrackUsage

# --- Artist ---
Index(
    "idx_artists_name", Artist.artist_name
)  # No longer a byproduct of unique=True -- artist_name isn't unique
# anymore (two real people can share a name; MB import disambiguates by
# MBID), so name lookups need their own explicit index.
Index("idx_artists_religion_id", Artist.religion_id)  # Grouped counts in religion_manager
Index(
    "idx_artists_begin_month_day", Artist.begin_month, Artist.begin_day
)  # "Most common birthdate (m+d)" statistic
Index(
    "idx_artists_end_month_day", Artist.end_month, Artist.end_day
)  # "Most common deathdate (m+d)" statistic
Index(
    "idx_artists_begin_year", Artist.begin_year
)  # Generation-bucket rating stat, oldest/youngest-artist stats

# --- Album ---
Index("idx_albums_title", Album.album_name)
Index("idx_albums_release_year", Album.release_year)  # Commonly filtered/sorted
Index(
    "idx_albums_release_month_day", Album.release_month, Album.release_day
)  # "Most common album release date (m+d)" statistic
Index(
    "idx_albums_release_country", Album.release_country
)  # Release-country distribution / highest-rated-album-by-country statistics

# --- Track ---
Index("idx_tracks_title", Track.track_name)
Index("idx_track_album_id", Track.album_id)
Index("idx_track_disc_id", Track.disc_id)
Index("idx_tracks_track_number", Track.track_number)  # Used in sort and gap detection
Index("idx_tracks_play_count", Track.play_count)  # Useful for "most played" queries
Index("idx_tracks_user_rating", Track.user_rating)  # Useful for "top rated" queries
Index("idx_tracks_needs_tag_write", Track.needs_tag_write)  # Dirty-tracking scan filter

# --- Genre ---
Index("idx_genres_name", Genre.genre_name)
Index(
    "idx_genres_parent_id", Genre.parent_id
)  # Genre hierarchy walk: most-niche-genre depth, root-branch lookups

# --- Mood ---
Index("idx_moods_parent_id", Mood.parent_id)  # Mood hierarchy walk

# --- Disc ---
Index("idx_discs_album_number", Disc.album_id, Disc.disc_number)

# --- Junction tables ---
Index("idx_track_artist_roles", TrackArtistRole.artist_id, TrackArtistRole.track_id)
Index("idx_album_roles_artist", AlbumRoleAssociation.artist_id)  # Reverse lookup: artist → albums
Index("idx_track_genres_genre_id", TrackGenre.genre_id)  # Reverse lookup: genre → tracks
Index(
    "idx_mood_track_association_track_id", MoodTrackAssociation.track_id
)  # Reverse lookup: track → moods
Index(
    "idx_artist_type_associations_type_id", ArtistTypeAssociation.artist_type_id
)  # Reverse lookup: type → artists (artist-type distribution/rating statistics)
Index(
    "idx_track_artist_roles_role_id", TrackArtistRole.role_id
)  # Reverse lookup: role → credited tracks/artists (role-credit statistics)
Index(
    "idx_album_role_association_role_id", AlbumRoleAssociation.role_id
)  # Reverse lookup: role → credited albums/artists

# --- Artist relationship self-joins ---
Index(
    "idx_artist_influences_influenced_id", ArtistInfluence.influenced_id
)  # Reverse lookup: who did this artist influence
Index(
    "idx_group_membership_member_id", GroupMembership.member_id
)  # Reverse lookup: what groups is this artist a member of

# --- Publisher ---
Index("idx_album_publisher_publisher_id", AlbumPublisher.publisher_id)
Index(
    "idx_publisher_founders_artist_id", PublisherFounder.artist_id
)  # Reverse lookup: artist → publishers founded
Index("idx_publishers_parent_id", Publisher.parent_id)  # Publisher hierarchy walk

# --- Role ---
Index("idx_roles_parent_id", Role.parent_id)  # Role hierarchy walk

# --- Place ---
Index("idx_places_parent_id", Place.parent_id)  # Recursive place/country rollups
Index(
    "idx_places_place_type", Place.place_type
)  # Country-only filtering for recursive country rating statistics

# --- Place associations ---
Index("idx_place_associations", PlaceAssociation.place_id, PlaceAssociation.entity_id)
Index(
    "idx_place_assoc_entity_type_id", PlaceAssociation.entity_type, PlaceAssociation.entity_id
)  # Covers both entity_type-only sweeps and the common entity_type+entity_id lookup

# --- Awards ---
Index(
    "idx_awards_mb_series_id", Award.mb_series_id, Award.award_year
)  # Awards-sync find-or-create key

# --- Award associations ---
Index("idx_award_associations", AwardAssociation.award_id, AwardAssociation.entity_id)
Index(
    "idx_award_assoc_entity_type_id", AwardAssociation.entity_type, AwardAssociation.entity_id
)  # Covers both entity_type-only sweeps and the common entity_type+entity_id lookup

# --- Playlists ---
Index(
    "idx_playlist_tracks_track_id", PlaylistTracks.track_id
)  # Reverse lookup: track → playlists it appears in
Index(
    "idx_smart_playlist_criteria_playlist_id", SmartPlaylistCriteria.smart_playlist_id
)  # Loaded whenever a smart playlist is built/edited

# --- Charts ---
Index(
    "idx_chart_entries_entity", ChartEntry.entity_type, ChartEntry.entity_id
)  # Reverse lookup: track/album -> its chart history
Index(
    "idx_chart_entries_raw_title", ChartEntry.raw_title
)  # Search tab lookups; also the matcher's title-bucket build query

# --- Samples ---
Index("idx_samples_sampled_id", Samples.sampled_id)  # Reverse lookup: track → tracks that sample it

# --- TrackUsage ---
Index("idx_track_usages_track_id", TrackUsage.track_id)
