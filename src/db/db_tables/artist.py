"""
Artist-related ORM models: Artist, ArtistAlias, ArtistInfluence, and GroupMembership.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Artist(Base):
    __tablename__ = "artists"

    artist_id = Column(Integer, primary_key=True)
    artist_name = Column(String, unique=True)
    isgroup = Column(Integer, CheckConstraint("isgroup IN (0, 1)"))
    begin_year = Column(Integer)
    gender = Column(String)
    end_year = Column(Integer)
    is_fixed = Column(Integer)
    begin_month = Column(Integer)
    begin_day = Column(Integer)
    end_month = Column(Integer)
    end_day = Column(Integer)
    biography = Column(String)
    disambiguation = Column(String)
    MBID = Column(String)
    profile_pic_path = Column(String)
    wikipedia_link = Column(String)
    website_link = Column(String)
    religion_id = Column(
        Integer, ForeignKey("religions.religion_id", ondelete="SET NULL")
    )

    religion = relationship("Religion", back_populates="artists")
    aliases = relationship(
        "ArtistAlias",
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases_list = association_proxy("aliases", "alias_name")
    types = relationship(
        "ArtistType", secondary="artist_type_associations", back_populates="artists"
    )
    type_names = association_proxy("types", "type_name")
    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tracks = association_proxy("track_roles", "track")
    track_roles = relationship(
        "TrackArtistRole",
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Group membership relationships
    member_memberships = relationship(
        "GroupMembership",
        foreign_keys="GroupMembership.member_id",
        back_populates="member",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    group_memberships = relationship(
        "GroupMembership",
        foreign_keys="GroupMembership.group_id",
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Artist influence relationships (ADD THESE)
    influencer_relations = relationship(
        "ArtistInfluence",
        foreign_keys="ArtistInfluence.influencer_id",
        back_populates="influencer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    influenced_relations = relationship(
        "ArtistInfluence",
        foreign_keys="ArtistInfluence.influenced_id",
        back_populates="influenced",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    places = relationship(
        "Place",
        secondary="place_associations",
        primaryjoin="and_(Artist.artist_id == PlaceAssociation.entity_id, "
        "PlaceAssociation.entity_type == 'Artist')",
        secondaryjoin="PlaceAssociation.place_id == Place.place_id",
        passive_deletes=True,
    )

    awards = relationship(
        "Award",
        secondary="award_associations",
        primaryjoin="and_(Artist.artist_id == AwardAssociation.entity_id, "
        "AwardAssociation.entity_type == 'Artist')",
        secondaryjoin="AwardAssociation.award_id == Award.award_id",
        passive_deletes=True,
    )

    founded_publisher_associations = relationship(
        "PublisherFounder",
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    founded_publishers = association_proxy("founded_publisher_associations", "publisher")

    @property
    def albums(self):
        """Return albums where this artist is an Album Artist (role_id=1)."""
        return [
            assoc.album
            for assoc in self.album_roles
            if assoc.role_id == 1 and assoc.album is not None
        ]

    @property
    def age(self):
        """Calculate artist's age or age at end."""
        if self.begin_year:
            end_year = self.end_year or datetime.now().year
            return end_year - self.begin_year
        return None

    @property
    def track_count(self):
        """Get total number of tracks by this artist."""
        return len(self.tracks) if self.tracks else 0

    @property
    def is_active(self):
        """Return True if the artist has no recorded end year (still active)."""
        return self.end_year is None

    @property
    def career_span(self):
        """Return a human-readable career span string, e.g. '2001–2015' or '2001–present'."""
        if not self.begin_year:
            return None
        end = str(self.end_year) if self.end_year else "present"
        return f"{self.begin_year}–{end}"

    @property
    def album_count(self):
        """Return the number of albums this artist is credited as Album Artist on."""
        return len(self.albums)


class ArtistAlias(Base):
    __tablename__ = "artist_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    alias_type = Column(
        String
    )  # Legal Name, Stylized name, Project Name, Persona, Birth Name, Former Name, Localized name, Romanized name, Phonetic name
    artist_id = Column(
        Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"), nullable=False
    )

    artist = relationship("Artist", back_populates="aliases")
    artist_name = association_proxy("artist", "artist_name")


class ArtistInfluence(Base):
    __tablename__ = "artist_influences"

    influencer_id = Column(Integer, ForeignKey("artists.artist_id"), primary_key=True)
    influenced_id = Column(Integer, ForeignKey("artists.artist_id"), primary_key=True)
    description = Column(Text)

    influencer = relationship(
        "Artist", foreign_keys=[influencer_id], back_populates="influencer_relations"
    )
    influenced = relationship(
        "Artist", foreign_keys=[influenced_id], back_populates="influenced_relations"
    )


class GroupMembership(Base):
    __tablename__ = "group_membership"

    group_id = Column(Integer, ForeignKey("artists.artist_id"), primary_key=True)
    member_id = Column(Integer, ForeignKey("artists.artist_id"), primary_key=True)
    role = Column(String)
    active_start_year = Column(Integer)
    active_end_year = Column(Integer)
    is_current = Column(Integer, CheckConstraint("is_current IN (0, 1)"))

    member = relationship(
        "Artist", foreign_keys=[member_id], back_populates="member_memberships"
    )
    group = relationship(
        "Artist", foreign_keys=[group_id], back_populates="group_memberships"
    )
