"""Album-related ORM models: Album, AlbumAlias, and virtual track links."""

from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base, oxford_join


class Album(Base):
    """One release (pressing) of an album."""

    __tablename__ = "albums"

    album_id = Column(Integer, primary_key=True)
    album_name = Column(String)
    album_language = Column(String)
    album_subtitle = Column(String)
    MBID = Column(String)
    release_group_MBID = Column(String)
    release_type = Column(String)
    release_country = Column(String)
    # MusicBrainz per-medium `format` verbatim ("CD", '12" Vinyl'); mixed sets are sorted and "/"-joined.
    media_format = Column(String)
    album_description = Column(String)
    release_year = Column(Integer)
    release_month = Column(Integer, CheckConstraint("release_month BETWEEN 1 AND 12"))
    release_day = Column(Integer, CheckConstraint("release_day BETWEEN 1 AND 31"))
    catalog_number = Column(String)
    first_pass = Column(Integer, CheckConstraint("first_pass IN (0, 1)"))
    second_pass = Column(Integer, CheckConstraint("second_pass IN (0, 1)"))
    album_gain = Column(Float)
    album_peak = Column(Float)
    album_wikipedia_link = Column(String)
    is_live = Column(Integer, CheckConstraint("is_live IN (0, 1)"))
    is_compilation = Column(Integer, CheckConstraint("is_compilation IN (0, 1)"))
    art_is_explicit = Column(Integer, CheckConstraint("art_is_explicit IN (0, 1)"))  # Cover/liner art contains explicit imagery
    estimated_sales = Column(Integer)
    status = Column(String)  # official, promotion, bootleg, withdrawn, expunged, cancelled
    discogs_master_url = Column(String)

    album_roles = relationship(
        "AlbumRoleAssociation", back_populates="album", cascade="all, delete-orphan", passive_deletes=True, order_by="AlbumRoleAssociation.sort_order, AlbumRoleAssociation.association_id"
    )
    tracks = relationship("Track", back_populates="album")
    discs = relationship("Disc", back_populates="album", cascade="all, delete-orphan", passive_deletes=True)
    publisher_associations = relationship("AlbumPublisher", back_populates="album", cascade="all, delete-orphan", passive_deletes=True)
    publishers = association_proxy("publisher_associations", "publisher")
    # viewonly: writes to place_associations always go through PlaceAssociation
    # objects directly (see Place.associations), never through this collection.
    places = relationship(
        "Place",
        secondary="place_associations",
        primaryjoin="and_(Album.album_id == PlaceAssociation.entity_id, PlaceAssociation.entity_type == 'Album')",
        secondaryjoin="PlaceAssociation.place_id == Place.place_id",
        viewonly=True,
    )

    # viewonly: writes to award_associations always go through AwardAssociation
    # objects directly (see Award.associations), never through this collection.
    awards = relationship(
        "Award",
        secondary="award_associations",
        primaryjoin="and_(Album.album_id == AwardAssociation.entity_id, AwardAssociation.entity_type == 'Album')",
        secondaryjoin="AwardAssociation.award_id == Award.award_id",
        viewonly=True,
    )
    album_aliases = relationship("AlbumAlias", back_populates="album", cascade="all, delete-orphan")
    virtual_track_links = relationship("AlbumVirtualTrack", back_populates="album", cascade="all, delete-orphan")

    @property
    def album_artists(self):
        """Artists credited as 'Album Artist'."""
        return [assoc.artist for assoc in self.album_roles if assoc.role and assoc.role.role_name == "Album Artist"]

    @property
    def total_duration(self):
        """Total album duration from its tracks."""
        return sum(track.duration or 0 for track in self.tracks)

    @property
    def album_artist_names(self):
        """Album artist names joined Oxford-comma style, or 'Unknown Artist'."""
        names = [assoc.credited_name or "Unknown Artist" for assoc in self.album_roles if assoc.role and assoc.role.role_name == "Album Artist"]
        names = [name.strip() for name in names if name and name.strip()]
        return oxford_join(names) if names else "Unknown Artist"

    @property
    def total_plays(self):
        """Total play count across all tracks in the album."""
        return sum(track.play_count or 0 for track in self.tracks)

    @property
    def average_rating(self):
        """Average user rating across rated tracks, or None."""
        ratings = [track.user_rating for track in self.tracks if track.user_rating is not None]
        if ratings:
            return sum(ratings) / len(ratings)
        return None

    @property
    def track_count(self):
        """Number of tracks in the album."""
        return len(self.tracks)

    @property
    def RIAA_certification(self):
        """RIAA certification level for estimated_sales (Gold, Platinum, multi-Platinum, Diamond)."""
        sales = max(int(self.estimated_sales or 0), 0)

        if sales < 500_000:
            return "None"
        if sales < 1_000_000:
            return "Gold"
        if sales >= 10_000_000:
            return "Diamond"
        platinum_count = sales // 1_000_000
        return f"{platinum_count}\u00d7 Platinum" if platinum_count > 1 else "Platinum"

    @property
    def full_tracklist(self):
        """Own and borrowed tracks in (disc, track number) order; unnumbered tracks last."""

        def key(disc_number, track_number):
            return (disc_number is None, disc_number or 0, track_number is None, track_number or 0)

        # Borrowed tracks sort by their position on this album, not on their source album.
        keyed = [(key(t.disc_number, t.track_number), t) for t in self.tracks]
        keyed += [(key(link.virtual_disc_number, link.virtual_track_number), link.track) for link in self.virtual_track_links if link.track is not None]
        return [track for _key, track in sorted(keyed, key=lambda pair: pair[0])]

    @property
    def possibly_incomplete(self):
        """True if any disc has a track-number gap (or only one track exists); None if unnumbered."""
        by_disc = {}
        for t in self.tracks:
            if t.track_number is not None:
                by_disc.setdefault(t.disc_id, set()).add(t.track_number)

        if not by_disc:
            return None

        if sum(len(numbers) for numbers in by_disc.values()) == 1:
            return True  # a lone track is suspicious

        # Track numbers restart on each disc, so check each disc on its own.
        return any(numbers != set(range(1, max(numbers) + 1)) for numbers in by_disc.values())

    @property
    def has_all_track_numbers(self):
        """True if every track on this album has a track_number."""
        return all(t.track_number is not None for t in self.tracks)

    @property
    def disc_count(self):
        """Number of discs on this album."""
        return len(self.discs)


class AlbumAlias(Base):
    """Alternate name that always resolves to one album."""

    __tablename__ = "album_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    alias_type = Column(String)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), nullable=False)

    album = relationship("Album", back_populates="album_aliases")
    album_name = association_proxy("album", "album_name")


class AlbumVirtualTrack(Base):
    """A track that an album borrows from another album (e.g. a "Greatest Hits" appearance)."""

    __tablename__ = "album_virtual_tracks"

    virtual_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), nullable=False)  # The album that borrows the track
    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), nullable=False)  # The borrowed track

    # Position of the track on the borrowing album
    virtual_track_number = Column(Integer)
    virtual_disc_number = Column(Integer)
    virtual_side = Column(String)
    virtual_absolute_track_number = Column(Integer)

    album = relationship("Album", back_populates="virtual_track_links")
    track = relationship("Track", back_populates="virtual_appearances")
