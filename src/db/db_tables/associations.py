"""
Plain many-to-many junction tables that don't own their own domain concept:
album<->publisher, track<->genre, track<->artist(role), album<->artist(role).
"""

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class AlbumPublisher(Base):
    __tablename__ = "album_publisher"

    album_id = Column(
        Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), primary_key=True
    )

    publisher_id = Column(
        Integer, ForeignKey("publishers.publisher_id"), primary_key=True
    )

    album = relationship("Album", back_populates="publisher_associations")
    publisher = relationship("Publisher", back_populates="album_associations")


class TrackGenre(Base):
    __tablename__ = "track_genres"

    track_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True
    )
    genre_id = Column(
        Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), primary_key=True
    )


class TrackArtistRole(Base):
    __tablename__ = "track_artist_roles"

    track_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True
    )
    artist_id = Column(
        Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True
    )
    role_id = Column(
        Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True
    )

    track = relationship("Track", back_populates="artist_roles")
    artist = relationship("Artist", back_populates="track_roles")
    role = relationship("Role", back_populates="track_roles")


class AlbumRoleAssociation(Base):
    __tablename__ = "album_role_association"
    __table_args__ = (
        # Prevent the same artist being added in the same role on the same album twice
        UniqueConstraint(
            "album_id", "artist_id", "role_id", name="uq_album_artist_role"
        ),
    )

    association_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"))
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"))
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"))

    # Relationships
    album = relationship("Album", back_populates="album_roles")
    artist = relationship("Artist", back_populates="album_roles")
    role = relationship("Role", back_populates="album_roles")
