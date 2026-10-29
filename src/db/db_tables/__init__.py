"""SQLAlchemy ORM schema, re-exported so callers can `from src.db.db_tables import X`."""

# indexes imports every model it indexes itself, so import order here is free.
from src.db.db_tables import indexes  # noqa: F401
from src.db.db_tables.album import Album, AlbumAlias, AlbumVirtualTrack
from src.db.db_tables.artist import Artist, ArtistAlias, ArtistInfluence, ArtistSplitAlias, GroupMembership
from src.db.db_tables.artist_type import ArtistType
from src.db.db_tables.associations import AlbumPublisher, AlbumRoleAssociation, ArtistTagAssociation, ArtistTypeAssociation, PublisherFounder, TrackArtistRole, TrackGenre
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base, set_sqlite_pragma
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.database import MusicDatabase
from src.db.db_tables.disc import Disc
from src.db.db_tables.genre import Genre, GenreAlias, GenreSplitAlias
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.playlist import Playlist, PlaylistTracks, SmartPlaylist, SmartPlaylistCriteria
from src.db.db_tables.publisher import Publisher, PublisherAlias, PublisherSplitAlias
from src.db.db_tables.role import Role, RoleAlias, RoleSplitAlias
from src.db.db_tables.tag import Tag, TagType
from src.db.db_tables.track import Samples, Track, TrackUsage

__all__ = [
    "Album",
    "AlbumAlias",
    "AlbumPublisher",
    "AlbumRoleAssociation",
    "AlbumVirtualTrack",
    "Artist",
    "ArtistAlias",
    "ArtistInfluence",
    "ArtistSplitAlias",
    "ArtistTagAssociation",
    "ArtistType",
    "ArtistTypeAssociation",
    "Award",
    "AwardAssociation",
    "Base",
    "Chart",
    "ChartEntry",
    "Disc",
    "Genre",
    "GenreAlias",
    "GenreSplitAlias",
    "GroupMembership",
    "Mood",
    "MoodTrackAssociation",
    "MusicDatabase",
    "Place",
    "PlaceAssociation",
    "PlaceAssociationType",
    "Playlist",
    "PlaylistTracks",
    "Publisher",
    "PublisherAlias",
    "PublisherFounder",
    "PublisherSplitAlias",
    "Role",
    "RoleAlias",
    "RoleSplitAlias",
    "Samples",
    "SmartPlaylist",
    "SmartPlaylistCriteria",
    "Tag",
    "TagType",
    "Track",
    "TrackArtistRole",
    "TrackGenre",
    "TrackUsage",
    "set_sqlite_pragma",
]
