"""Many-to-many junction tables that do not own a domain concept of their own."""

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class CreditedNameMixin:
    """Adds credited_name to a credit row with artist and credited_alias relationships."""

    @property
    def credited_name(self):
        """The overriding alias name if set, otherwise the artist's canonical name."""
        if self.credited_alias:
            return self.credited_alias.alias_name
        return self.artist.artist_name if self.artist else None


class AlbumPublisher(Base):
    """Link between an album and a publisher."""

    __tablename__ = "album_publisher"

    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), primary_key=True)
    publisher_id = Column(Integer, ForeignKey("publishers.publisher_id"), primary_key=True)

    album = relationship("Album", back_populates="publisher_associations")
    publisher = relationship("Publisher", back_populates="album_associations")


class TrackGenre(Base):
    """Link between a track and a genre."""

    __tablename__ = "track_genres"

    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), primary_key=True)


class ArtistTypeAssociation(Base):
    """Link between an artist and an ArtistType."""

    __tablename__ = "artist_type_associations"

    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True)
    artist_type_id = Column(Integer, ForeignKey("artist_types.artist_type_id", ondelete="CASCADE"), primary_key=True)


class ArtistTagAssociation(Base):
    """Link between an artist and a tag."""

    __tablename__ = "artist_tag_associations"

    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.tag_id", ondelete="CASCADE"), primary_key=True)


class TrackArtistRole(CreditedNameMixin, Base):
    """An artist credited on a track in one role."""

    __tablename__ = "track_artist_roles"

    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True)
    # Optional: show one of the artist's aliases on this credit only.
    credited_alias_id = Column(Integer, ForeignKey("artist_alias.alias_id", ondelete="SET NULL"))

    track = relationship("Track", back_populates="artist_roles")
    artist = relationship("Artist", back_populates="track_roles")
    role = relationship("Role", back_populates="track_roles")
    credited_alias = relationship("ArtistAlias")


class PublisherFounder(Base):
    """An artist credited as a founder of a record company (Publisher)."""

    __tablename__ = "publisher_founders"

    publisher_id = Column(Integer, ForeignKey("publishers.publisher_id", ondelete="CASCADE"), primary_key=True)
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), primary_key=True)

    publisher = relationship("Publisher", back_populates="founder_associations")
    artist = relationship("Artist", back_populates="founded_publisher_associations")


class AlbumRoleAssociation(CreditedNameMixin, Base):
    """An artist credited on an album in one role."""

    __tablename__ = "album_role_association"
    __table_args__ = (
        # Prevent the same artist being added in the same role on the same album twice
        UniqueConstraint("album_id", "artist_id", "role_id", name="uq_album_artist_role"),
    )

    association_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"))
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"))
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"))
    # Order among artists in the same (album_id, role_id) group, e.g. "A & B" vs "B & A".
    sort_order = Column(Integer, nullable=False, default=0)
    # Optional: show one of the artist's aliases on this credit only.
    credited_alias_id = Column(Integer, ForeignKey("artist_alias.alias_id", ondelete="SET NULL"))

    album = relationship("Album", back_populates="album_roles")
    artist = relationship("Artist", back_populates="album_roles")
    role = relationship("Role", back_populates="album_roles")
    credited_alias = relationship("ArtistAlias")
