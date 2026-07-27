"""
Defines the database schema using SQLAlchemy ORM.

The schema is split by domain across this package's modules; everything is
re-exported here so existing code can keep doing `from src.db.db_tables import X`
or `import src.db.db_tables` unchanged.
"""

from src.db.db_tables.album import Album, AlbumAlias, AlbumVirtualTrack
from src.db.db_tables.artist import (
    Artist,
    ArtistAlias,
    ArtistInfluence,
    GroupMembership,
)
from src.db.db_tables.artist_type import ArtistType
from src.db.db_tables.associations import (
    AlbumPublisher,
    AlbumRoleAssociation,
    ArtistTypeAssociation,
    PublisherFounder,
    TrackArtistRole,
    TrackGenre,
)
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base, set_sqlite_pragma
from src.db.db_tables.database import MusicDatabase
from src.db.db_tables.disc import Disc
from src.db.db_tables.genre import Genre, GenreAlias
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.playlist import (
    Playlist,
    PlaylistTracks,
    SmartPlaylist,
    SmartPlaylistCriteria,
)
from src.db.db_tables.publisher import Publisher, PublisherAlias
from src.db.db_tables.religion import Religion
from src.db.db_tables.role import Role
from src.db.db_tables.track import Samples, Track, TrackUsage

# Registers all Index(...) definitions against the tables above; must be
# imported last, after every model class exists.
from src.db.db_tables import indexes  # noqa: E402, F401

__all__ = [
    "Base",
    "set_sqlite_pragma",
    "MusicDatabase",
    "Album",
    "AlbumAlias",
    "AlbumVirtualTrack",
    "Samples",
    "Track",
    "TrackUsage",
    "Disc",
    "Artist",
    "ArtistAlias",
    "ArtistInfluence",
    "ArtistType",
    "ArtistTypeAssociation",
    "GroupMembership",
    "Genre",
    "GenreAlias",
    "Mood",
    "MoodTrackAssociation",
    "Publisher",
    "PublisherAlias",
    "Religion",
    "Place",
    "PlaceAssociation",
    "PlaceAssociationType",
    "Award",
    "AwardAssociation",
    "Playlist",
    "SmartPlaylist",
    "PlaylistTracks",
    "SmartPlaylistCriteria",
    "Role",
    "AlbumPublisher",
    "PublisherFounder",
    "TrackGenre",
    "TrackArtistRole",
    "AlbumRoleAssociation",
]
