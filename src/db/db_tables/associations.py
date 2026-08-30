"""
Plain many-to-many junction tables that don't own their own domain concept:
album<->publisher, track<->genre, track<->artist(role), album<->artist(role).
"""

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class AlbumPublisher(Base):
    __tablename__ = "album_publisher"

    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), primary_key=True)

    publisher_id = Column(Integer, ForeignKey("publishers.publisher_id"), primary_key=True)

    album = relationship("Album", back_populates="publisher_associations")
    publisher = relationship("Publisher", back_populates="album_associations")


class TrackGenre(Base):
    __tablename__ = "track_genres"

    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), primary_key=True)


class ArtistTypeAssociation(Base):
    __tablename__ = "artist_type_associations"

    artist_id = Column(
        Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True
    )
    artist_type_id = Column(
        Integer, ForeignKey("artist_types.artist_type_id", ondelete="CASCADE"), primary_key=True
    )


class TrackArtistRole(Base):
    __tablename__ = "track_artist_roles"

    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)
    artist_id = Column(
        Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True
    )
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True)
    # Optional override so this specific credit can display/write one of the
    # artist's existing aliases (e.g. "The Artist Formerly Known As") instead
    # of their canonical artist_name, without changing who they resolve to.
    credited_alias_id = Column(Integer, ForeignKey("artist_alias.alias_id", ondelete="SET NULL"))

    track = relationship("Track", back_populates="artist_roles")
    artist = relationship("Artist", back_populates="track_roles")
    role = relationship("Role", back_populates="track_roles")
    credited_alias = relationship("ArtistAlias")

    @property
    def credited_name(self):
        """Name to display/write for this credit: the overriding alias if
        set, otherwise the artist's canonical name."""
        if self.credited_alias:
            return self.credited_alias.alias_name
        return self.artist.artist_name if self.artist else None


class PublisherFounder(Base):
    """An artist credited as a founder of a record company (Publisher)."""

    __tablename__ = "publisher_founders"

    publisher_id = Column(
        Integer, ForeignKey("publishers.publisher_id", ondelete="CASCADE"), primary_key=True
    )
    artist_id = Column(
        Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True
    )

    publisher = relationship("Publisher", back_populates="founder_associations")
    artist = relationship("Artist", back_populates="founded_publisher_associations")


class AlbumRoleAssociation(Base):
    __tablename__ = "album_role_association"
    __table_args__ = (
        # Prevent the same artist being added in the same role on the same album twice
        UniqueConstraint("album_id", "artist_id", "role_id", name="uq_album_artist_role"),
    )

    association_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"))
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"))
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"))
    # Position of this artist among others sharing the same (album_id, role_id)
    # group -- lets "Duke Ellington & John Coltrane" be reordered to
    # "John Coltrane & Duke Ellington" without changing who's credited.
    sort_order = Column(Integer, nullable=False, default=0)
    # Optional override so this specific credit can display/write one of the
    # artist's existing aliases (e.g. "The Artist Formerly Known As") instead
    # of their canonical artist_name, without changing who they resolve to.
    credited_alias_id = Column(Integer, ForeignKey("artist_alias.alias_id", ondelete="SET NULL"))

    # Relationships
    album = relationship("Album", back_populates="album_roles")
    artist = relationship("Artist", back_populates="album_roles")
    role = relationship("Role", back_populates="album_roles")
    credited_alias = relationship("ArtistAlias")

    @property
    def credited_name(self):
        """Name to display/write for this credit: the overriding alias if
        set, otherwise the artist's canonical name."""
        if self.credited_alias:
            return self.credited_alias.alias_name
        return self.artist.artist_name if self.artist else None
