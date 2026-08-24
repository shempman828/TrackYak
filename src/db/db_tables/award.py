"""
Award ORM models: Award (self-referential hierarchy) and its polymorphic association table.
"""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Award(Base):
    __tablename__ = "awards"

    award_id = Column(Integer, primary_key=True)
    award_name = Column(String(100), nullable=False)
    award_year = Column(Integer)
    award_category = Column(String(100))
    award_description = Column(Text)
    wikipedia_link = Column(String)
    parent_id = Column(Integer, ForeignKey("awards.award_id", ondelete="SET NULL"))
    # MusicBrainz series MBID this award category was imported from -- the
    # find-or-create key for awards sync, and a provenance marker that keeps
    # resync from ever touching a manually-curated award with the same
    # category+year (those have no mb_series_id).
    mb_series_id = Column(String)

    parent = relationship("Award", remote_side=[award_id], backref="children")
    associations = relationship(
        "AwardAssociation",
        back_populates="award",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    tracks = relationship(
        "Track",
        secondary="award_associations",
        primaryjoin="and_(Award.award_id == AwardAssociation.award_id, "
        "AwardAssociation.entity_type == 'Track')",
        secondaryjoin="AwardAssociation.entity_id == Track.track_id",
        viewonly=True,
    )

    artists = relationship(
        "Artist",
        secondary="award_associations",
        primaryjoin="and_(Award.award_id == AwardAssociation.award_id, "
        "AwardAssociation.entity_type == 'Artist')",
        secondaryjoin="AwardAssociation.entity_id == Artist.artist_id",
        viewonly=True,
    )

    @property
    def recipients(self):
        """Return all associated entities marked as recipients."""
        return [
            assoc.entity
            for assoc in self.associations
            if assoc.association_type == "recipient" and assoc.entity is not None
        ]


class AwardAssociation(Base):
    __tablename__ = "award_associations"

    association_id = Column(Integer, primary_key=True)
    award_id = Column(Integer, ForeignKey("awards.award_id"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_type = Column(
        String,
        CheckConstraint("entity_type IN ('Artist', 'Track', 'Album', 'Publisher')"),
        nullable=False,
    )
    association_type = Column(String)
    # MBID of the MusicBrainz recording/release-group/artist this
    # association was derived from -- provenance only, awards sync
    # de-duplicates on (award_id, entity_type, entity_id), not this column.
    mb_target_mbid = Column(String)

    award = relationship("Award", back_populates="associations")

    artist = relationship(
        "Artist",
        primaryjoin="and_(AwardAssociation.entity_id == foreign(Artist.artist_id), "
        "AwardAssociation.entity_type == 'Artist')",
        viewonly=True,
    )
    album = relationship(
        "Album",
        primaryjoin="and_(AwardAssociation.entity_id == foreign(Album.album_id), "
        "AwardAssociation.entity_type == 'Album')",
        viewonly=True,
    )
    track = relationship(
        "Track",
        primaryjoin="and_(AwardAssociation.entity_id == foreign(Track.track_id), "
        "AwardAssociation.entity_type == 'Track')",
        viewonly=True,
    )
    publisher = relationship(
        "Publisher",
        primaryjoin="and_(AwardAssociation.entity_id == foreign(Publisher.publisher_id), "
        "AwardAssociation.entity_type == 'Publisher')",
        viewonly=True,
    )
    playlist = relationship(
        "Playlist",
        primaryjoin="and_(AwardAssociation.entity_id == foreign(Playlist.playlist_id), "
        "AwardAssociation.entity_type == 'Playlist')",
        viewonly=True,
    )

    @property
    def entity(self):
        """Return the actual entity object dynamically."""
        entity_getters = {
            "Artist": self.artist,
            "Album": self.album,
            "Track": self.track,
            "Publisher": self.publisher,
            "Playlist": self.playlist,
        }
        return entity_getters.get(self.entity_type)
