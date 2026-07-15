"""
Track-related ORM models: Samples (self-referential sampling), Track, and TrackUsage.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import CheckConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Samples(Base):
    __tablename__ = "samples"
    sampled_by_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True
    )
    sampled_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True
    )
    # relationships back to Track
    sampled_by = relationship(
        "Track",
        foreign_keys=[sampled_by_id],
        back_populates="samples_used",
    )
    sampled = relationship(
        "Track",
        foreign_keys=[sampled_id],
        back_populates="sampled_by_tracks",
    )


class Track(Base):
    __tablename__ = "tracks"

    # Basic Properties
    track_id = Column(Integer, primary_key=True)
    track_name = Column(String, nullable=False)
    track_number = Column(Integer)  # Track position on disc
    absolute_track_number = Column(Integer)  # Overall track position in album
    side = Column(String)  # e.g., "A", "B" for vinyl sides
    track_file_path = Column(String, unique=True)
    duration = Column(Float)
    file_size = Column(Float)
    file_extension = Column(String)
    disc_id = Column(Integer, ForeignKey("discs.disc_id", ondelete="SET NULL"))
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="SET NULL"))

    # Expanded Names
    track_name_original = Column(String)  # "稲妻ブルース"
    track_name_transcribed = Column(String)  # "Inazuma Burūsu"
    track_name_translated = Column(String)  # "Lightning Blues"
    track_name_official = Column(String)  # "INAZUMA BLUES"
    track_name_stylized = Column(String)  # "稲妻B L U E S"

    # Date Metadata
    recorded_year = Column(Integer)
    recorded_month = Column(Integer)
    recorded_day = Column(Integer)
    remaster_year = Column(Integer)
    composed_year = Column(Integer)
    composed_month = Column(Integer)
    composed_day = Column(Integer)

    # Classical Metadata
    is_classical = Column(Integer)
    work_name = Column(String)
    work_type = Column(String)
    classical_catalog_prefix = Column(String)
    classical_catalog_number = Column(Integer)
    first_performed_year = Column(Integer)
    classical_tempo = Column(String)
    movement_name = Column(String)
    movement_number = Column(Integer)

    # Identification Metadata
    isrc = Column(String)
    track_copyright = Column(String)
    MBID = Column(String)
    track_barcode = Column(String)
    track_wikipedia_link = Column(String)

    # Audio Characteristics
    bpm = Column(Float)
    track_gain = Column(Float)
    track_peak = Column(Float)
    crest_factor = Column(Float)  # Peak-to-loudness ratio in dB
    key = Column(String)  # Musical key (C, D, E, etc.)
    mode = Column(String)  # Major or minor
    primary_time_signature = Column(String)  # e.g., 4/4, 3/4
    key_confidence = Column(Float)
    tempo_confidence = Column(Float)  # Confidence in BPM detection

    # Quality Data
    user_rating = Column(Float)
    bit_rate = Column(Integer)
    sample_rate = Column(Integer)
    bit_depth = Column(Integer)
    channels = Column(Integer)
    last_listened_date = Column(DateTime)
    is_fixed = Column(Integer)
    track_quality = Column(String)

    # User Metadata
    comment = Column(String)
    date_added = Column(DateTime, default=datetime.now)
    play_count = Column(Integer)

    lyrics = Column(String)
    track_description = Column(String)
    is_explicit = Column(Integer)
    is_instrumental = Column(Integer)

    # Spectral analysis
    spectral_centroid = Column(Float)  # Brightness of sound
    spectral_rolloff = Column(Float)  # Frequency cutoff
    spectral_flatness = Column(Float)  # Range: 0.0 (tonal) - 1.0 (noise-like)
    spectral_flux = Column(Float)  # Range: 0.0 - 1.0, frame-to-frame spectral change

    # Advanced features
    dynamic_range = Column(Float)  # Range: 6.0 - 20.0+
    stereo_width = Column(Float)  # Range: 0.0 - 1.0
    ms_energy_ratio = Column(Float)  # Raw side/mid RMS ratio, unbounded (~0.0 - 2.0)
    channel_coherence = Column(Float)  # Range: 0.0 - 1.0, similarity between L/R
    transient_strength = Column(Float)  # Range: 0.0 - 0.5+
    danceability = Column(Float)  # 0-1 how danceable
    energy = Column(Float)  # 0-1 intensity/activity
    acousticness = Column(Float)  # 0-1 acoustic vs electric
    liveness = Column(Float)  # 0-1 performed live
    valence = Column(Float)  # 0-1 musical positiveness
    fidelity_score = Column(Float)  # 1 - (RMS_compression + spectral_flatness) * 0.5

    album = relationship("Album", back_populates="tracks")
    album_name = association_proxy("album", "album_name")
    release_year = association_proxy("album", "release_year")
    release_month = association_proxy("album", "release_month")
    release_day = association_proxy("album", "release_day")
    genres = relationship("Genre", secondary="track_genres", back_populates="tracks")
    genre_names = association_proxy("genres", "genre_name")
    artists = association_proxy("artist_roles", "artist")
    place_names = association_proxy("places", "place_name")
    artist_roles = relationship(
        "TrackArtistRole",
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    moods = relationship(
        "Mood", secondary="mood_track_association", back_populates="tracks"
    )

    places = relationship(
        "Place",
        secondary="place_associations",
        primaryjoin="and_(Track.track_id == PlaceAssociation.entity_id, "
        "PlaceAssociation.entity_type == 'Track')",
        secondaryjoin="PlaceAssociation.place_id == Place.place_id",
        back_populates="tracks",
        overlaps="associations",
        cascade="save-update",  # Limit cascade behavior
        passive_deletes=True,  # Let the database handle deletions
        viewonly=False,
    )
    awards = relationship(
        "Award",
        secondary="award_associations",
        primaryjoin="and_(Track.track_id == AwardAssociation.entity_id, "
        "AwardAssociation.entity_type == 'Track')",
        secondaryjoin="AwardAssociation.award_id == Award.award_id",
        passive_deletes=True,
    )

    playlists = relationship(
        "PlaylistTracks",
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Tracks that this track *samples*
    samples_used = relationship(
        "Samples",
        foreign_keys=[Samples.sampled_by_id],
        back_populates="sampled_by",
        cascade="all, delete-orphan",
    )

    # Tracks that *sample* this track
    sampled_by_tracks = relationship(
        "Samples",
        foreign_keys=[Samples.sampled_id],
        back_populates="sampled",
        cascade="all, delete-orphan",
    )
    virtual_appearances = relationship("AlbumVirtualTrack", back_populates="track")
    usages = relationship(
        "TrackUsage",
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @hybrid_property
    def primary_artists(self):
        """Return only the artists credited as "Primary" for this track."""
        return [
            assoc.artist
            for assoc in self.artist_roles
            if assoc.role and assoc.role.role_name == "Primary Artist"
        ]

    @property
    def primary_artist_names(self):
        """Return properly formatted primary artist names (Oxford comma style)."""
        names = [
            artist.artist_name
            if artist and hasattr(artist, "artist_name") and artist.artist_name
            else "Unknown Artist"
            for artist in self.primary_artists
        ]

        # Clean up whitespace and remove empty strings
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
    def composer_names(self):
        """Return list of composer names for this track."""
        return [
            assoc.artist.artist_name
            for assoc in self.artist_roles
            if assoc.role and assoc.role.role_name == "Composer"
        ]

    @property
    def disc_number(self):
        """Get disc number from associated disc."""
        return self.disc.disc_number if self.disc else None

    @property
    def publisher_names(self):
        """Return list of publisher names for this track's album."""
        if not self.album:
            return []
        return [pub.publisher_name for pub in self.album.publishers]

    @hybrid_property
    def sampled_tracks(self):
        """Tracks this track samples (returns a list of Track objects)."""
        return [s.sampled for s in self.samples_used]

    @hybrid_property
    def sampling_tracks(self):
        """Tracks that sample this track (returns a list of Track objects)."""
        return [s.sampled_by for s in self.sampled_by_tracks]

    @property
    def movement_number_roman(self):
        """Return the movement number as a Roman numeral, or None if missing."""
        if self.movement_number is None or self.movement_number <= 0:
            return None

        val_map = [
            (1000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        ]

        num = int(self.movement_number)
        roman = ""
        for value, numeral in val_map:
            while num >= value:
                roman += numeral
                num -= value

        return roman

    @property
    def all_albums(self):
        """Returns the primary album plus all albums that 'borrow' this track."""
        albums = [self.album] if self.album else []  #
        borrowed_albums = [link.album for link in self.virtual_appearances]
        return albums + borrowed_albums

    @property
    def duration_formatted(self):
        """Return duration in MM:SS format."""
        if self.duration is None:
            return "Unknown"
        minutes = int(self.duration) // 60
        seconds = int(self.duration) % 60
        return f"{minutes}:{seconds:02d}"

    @property
    def album_MBID(self):
        """Return the MusicBrainz ID of the album, if available."""
        return self.album.MBID if self.album and self.album.MBID else None

    @property
    def album_complete(self):
        """Return True if track has all album-level metadata filled in, False otherwise."""
        if not self.album:
            return False
        if self.album.is_fixed == 1:
            return True
        return False

    @property
    def is_complete(self):
        """Return True if the track has the minimum expected metadata filled in."""
        return bool(
            self.track_name
            and self.duration
            and self.artist_roles
            and self.album_id
            and self.track_number is not None
        )

    @property
    def has_audio_analysis(self):
        """Return True if the key audio analysis fields are populated."""
        return all(
            v is not None for v in [self.bpm, self.key, self.energy, self.danceability]
        )

    @property
    def age_in_years(self):
        """Return how many years ago the track was recorded, or None if unknown."""
        if self.recorded_year:
            return datetime.now().year - self.recorded_year
        return None

    @property
    def used_in_summary(self):
        """Return a short list of context labels for this track's usages."""
        if not self.usages:
            return []
        return [
            f"{u.usage_type}: {u.title}" + (f" ({u.year})" if u.year else "")
            for u in self.usages
        ]


class TrackUsage(Base):
    """Records external contexts where a track was used (soundtracks, events, ads, etc.)."""

    __tablename__ = "track_usages"

    usage_id = Column(Integer, primary_key=True)
    track_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), nullable=False
    )
    usage_type = Column(
        String,
        CheckConstraint(
            "usage_type IN ('Film', 'TV Show', 'Video Game', 'Live Event', 'Commercial', 'Other')"
        ),
        nullable=False,
    )
    title = Column(String, nullable=False)  # e.g. "Guardians of the Galaxy Vol. 2"
    year = Column(Integer)
    description = Column(Text)  # Extra context, e.g. "Plays during the credits scene"
    wikipedia_link = Column(String)

    track = relationship("Track", back_populates="usages")
