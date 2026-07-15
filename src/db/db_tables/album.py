"""
Album-related ORM models: Album, AlbumAlias, and virtual track links.
"""

from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Album(Base):
    __tablename__ = "albums"

    album_id = Column(Integer, primary_key=True)
    album_name = Column(String)
    album_language = Column(String)
    album_subtitle = Column(String)
    MBID = Column(String)
    release_type = Column(String)
    album_description = Column(String)
    release_year = Column(Integer)
    release_month = Column(Integer, CheckConstraint("release_month BETWEEN 1 AND 12"))
    release_day = Column(Integer, CheckConstraint("release_day BETWEEN 1 AND 31"))
    catalog_number = Column(String)
    is_fixed = Column(Integer, CheckConstraint("is_fixed IN (0, 1)"))
    album_gain = Column(Float)
    album_peak = Column(Float)
    album_wikipedia_link = Column(String)
    is_live = Column(Integer, CheckConstraint("is_live IN (0, 1)"))
    is_compilation = Column(Integer, CheckConstraint("is_compilation IN (0, 1)"))
    art_is_explicit = Column(
        Integer, CheckConstraint("art_is_explicit IN (0, 1)")
    )  # Cover/liner art contains explicit imagery
    estimated_sales = Column(Integer)
    status = Column(
        String
    )  # official, promotion, bootleg, withdrawn, expunged, cancelled

    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlbumRoleAssociation.sort_order, AlbumRoleAssociation.association_id",
    )
    tracks = relationship("Track", back_populates="album")
    publisher_associations = relationship(
        "AlbumPublisher",
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    publishers = association_proxy("publisher_associations", "publisher")
    places = relationship(
        "Place",
        secondary="place_associations",
        primaryjoin="and_(Album.album_id == PlaceAssociation.entity_id, "
        "PlaceAssociation.entity_type == 'Album')",
        secondaryjoin="PlaceAssociation.place_id == Place.place_id",
        passive_deletes=True,
    )

    awards = relationship(
        "Award",
        secondary="award_associations",
        primaryjoin="and_(Album.album_id == AwardAssociation.entity_id, "
        "AwardAssociation.entity_type == 'Album')",
        secondaryjoin="AwardAssociation.award_id == Award.award_id",
        passive_deletes=True,
    )
    album_aliases = relationship(
        "AlbumAlias",
        back_populates="album",
        cascade="all, delete-orphan",
    )
    virtual_track_links = relationship(
        "AlbumVirtualTrack", back_populates="album", cascade="all, delete-orphan"
    )

    @property
    def album_artists(self):
        """Return only the artists credited as 'Album Artist'."""
        return [
            assoc.artist
            for assoc in self.album_roles
            if assoc.role and assoc.role.role_name == "Album Artist"
        ]

    @hybrid_property
    def total_duration(self):
        """Calculate total album duration from tracks."""
        if self.tracks:
            return sum(track.duration or 0 for track in self.tracks)
        return 0

    @property
    def album_artist_names(self):
        """Return properly formatted album artist names (Oxford comma style)."""
        names = [
            artist.artist_name
            if artist and hasattr(artist, "artist_name") and artist.artist_name
            else "Unknown Artist"
            for artist in self.album_artists
        ]

        # Clean whitespace and remove empty values
        names = [name.strip() for name in names if name and name.strip()]

        if not names:
            return "Unknown Artist"

        if len(names) == 1:
            return names[0]

        if len(names) == 2:
            return f"{names[0]} & {names[1]}"

        # 3 or more → Oxford comma
        return f"{', '.join(names[:-1])}, & {names[-1]}"

    @property
    def total_plays(self):
        """Get total play count across all tracks in the album."""
        return sum(track.play_count or 0 for track in self.tracks)

    @property
    def average_rating(self):
        """Get average user rating across all tracks in the album."""
        ratings = [
            track.user_rating for track in self.tracks if track.user_rating is not None
        ]
        if ratings:
            return sum(ratings) / len(ratings)
        return None

    @property
    def track_count(self):
        """Get number of tracks in the album."""
        return len(self.tracks) if self.tracks else 0

    @property
    def RIAA_certification(self):
        """Determine RIAA certification based on estimated sales, including multi-Platinum."""
        sales = max(int(self.estimated_sales or 0), 0)

        if sales < 250_000:
            return "None"
        elif sales < 500_000:
            return "Silver"
        elif sales < 1_000_000:
            return "Gold"
        else:
            # Multi-Platinum calculation
            platinum_count = sales // 1_000_000
            return f"{platinum_count}× Platinum" if platinum_count > 1 else "Platinum"

    @property
    def full_tracklist(self):
        physical = self.tracks  # 1. Get the physical tracks you already have
        virtual = [
            link.track for link in self.virtual_track_links
        ]  # 2. Get the borrowed tracks from the new table
        combined = physical + virtual
        # Sort by track_number, pushing any tracks with no number to the end
        return sorted(
            combined, key=lambda x: (x.track_number is None, x.track_number or 0)
        )  # 3. Combine them and sort by track number

    @property
    def possibly_incomplete(self):
        if not self.tracks:
            return None

        numbered = sorted(
            t.track_number for t in self.tracks if t.track_number is not None
        )

        if not numbered:
            return None

        # suspicious: only one track
        if len(numbered) == 1:
            return True

        expected = set(range(1, max(numbered) + 1))
        actual = set(numbered)

        return actual != expected

    @property
    def has_all_track_numbers(self):
        """Return True if every track on this album has a track_number assigned."""
        if not self.tracks:
            return True
        return all(t.track_number is not None for t in self.tracks)

    @property
    def disc_count(self):
        """Return the number of discs associated with this album."""
        return len(self.discs) if self.discs else 0


class AlbumAlias(Base):
    __tablename__ = "album_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    alias_type = Column(String)
    album_id = Column(
        Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), nullable=False
    )

    album = relationship("Album", back_populates="album_aliases")
    album_name = association_proxy("album", "album_name")


class AlbumVirtualTrack(Base):
    """This table allows albums to borrow tracks from other albums"""

    __tablename__ = "album_virtual_tracks"

    virtual_id = Column(Integer, primary_key=True)
    album_id = Column(
        Integer, ForeignKey("albums.album_id", ondelete="CASCADE")
    )  # The album that "borrows" the track
    track_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE")
    )  # The actual track object

    # Context-specific metadata for the "Greatest Hits" appearance
    virtual_track_number = Column(Integer)
    virtual_disc_number = Column(Integer)
    virtual_side = Column(String)
    virtual_absolute_track_number = Column(Integer)

    album = relationship("Album", back_populates="virtual_track_links")
    track = relationship("Track", back_populates="virtual_appearances")
