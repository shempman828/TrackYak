"""Award ORM models: Award (self-referential hierarchy) and its polymorphic association table."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from src.db.db_tables.base import ENTITY_RELATIONSHIP_ATTRS, Base


class Award(Base):
    """Award or award category, nested under a parent award."""

    __tablename__ = "awards"

    award_id = Column(Integer, primary_key=True)
    award_name = Column(String(100), nullable=False)
    award_year = Column(Integer)
    award_category = Column(String(100))
    award_description = Column(Text)
    wikipedia_link = Column(String)
    parent_id = Column(Integer, ForeignKey("awards.award_id", ondelete="SET NULL"))
    # MusicBrainz series MBID: awards-sync find-or-create key. NULL marks a
    # manually-curated award, which resync never touches.
    mb_series_id = Column(String)

    parent = relationship("Award", remote_side=[award_id], backref="children")
    # No passive_deletes: older DB files have no ON DELETE on award_id, so the
    # ORM must load and delete these rows itself.
    associations = relationship("AwardAssociation", back_populates="award", cascade="all, delete-orphan")

    tracks = relationship(
        "Track",
        secondary="award_associations",
        primaryjoin="and_(Award.award_id == AwardAssociation.award_id, AwardAssociation.entity_type == 'Track')",
        secondaryjoin="AwardAssociation.entity_id == Track.track_id",
        viewonly=True,
    )

    artists = relationship(
        "Artist",
        secondary="award_associations",
        primaryjoin="and_(Award.award_id == AwardAssociation.award_id, AwardAssociation.entity_type == 'Artist')",
        secondaryjoin="AwardAssociation.entity_id == Artist.artist_id",
        viewonly=True,
    )

    @property
    def recipients(self):
        """All associated entities marked as recipients."""
        return [assoc.entity for assoc in self.associations if assoc.association_type == "recipient" and assoc.entity is not None]


class AwardAssociation(Base):
    """Polymorphic link from an award to an Artist, Track, Album, or Publisher."""

    __tablename__ = "award_associations"

    association_id = Column(Integer, primary_key=True)
    award_id = Column(Integer, ForeignKey("awards.award_id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_type = Column(String, CheckConstraint("entity_type IN ('Artist', 'Track', 'Album', 'Publisher')"), nullable=False)
    association_type = Column(String)
    # Provenance only: awards sync de-duplicates on (award_id, entity_type, entity_id).
    mb_target_mbid = Column(String)

    award = relationship("Award", back_populates="associations")

    # uselist=False: foreign() on the remote side otherwise makes each of these a list.
    artist = relationship("Artist", primaryjoin="and_(AwardAssociation.entity_id == foreign(Artist.artist_id), AwardAssociation.entity_type == 'Artist')", uselist=False, viewonly=True)
    album = relationship("Album", primaryjoin="and_(AwardAssociation.entity_id == foreign(Album.album_id), AwardAssociation.entity_type == 'Album')", uselist=False, viewonly=True)
    track = relationship("Track", primaryjoin="and_(AwardAssociation.entity_id == foreign(Track.track_id), AwardAssociation.entity_type == 'Track')", uselist=False, viewonly=True)
    publisher = relationship("Publisher", primaryjoin="and_(AwardAssociation.entity_id == foreign(Publisher.publisher_id), AwardAssociation.entity_type == 'Publisher')", uselist=False, viewonly=True)
    playlist = relationship("Playlist", primaryjoin="and_(AwardAssociation.entity_id == foreign(Playlist.playlist_id), AwardAssociation.entity_type == 'Playlist')", uselist=False, viewonly=True)

    @property
    def entity(self):
        """The linked entity object, loading only the relationship for entity_type."""
        attr = ENTITY_RELATIONSHIP_ATTRS.get(self.entity_type)
        return getattr(self, attr) if attr else None
