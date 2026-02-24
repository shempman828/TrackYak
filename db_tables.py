"""
Defines the database schema using SQLAlchemy ORM.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import backref, declarative_base, relationship, sessionmaker

from logger_config import logger

Base = declarative_base()


# Enable SQLite foreign key support
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    front_cover_path = Column(String)
    rear_cover_path = Column(String)
    album_liner_path = Column(String)
    album_wikipedia_link = Column(String)
    is_live = Column(Integer, CheckConstraint("is_live IN (0, 1)"))
    is_compilation = Column(Integer, CheckConstraint("is_compilation IN (0, 1)"))
    estimated_sales = Column(Integer)
    status = Column(
        String
    )  # official, promotion, bootleg,withdrawn, expunged, cancelled

    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
        """Return list of artist names for this album, handling None values."""
        names = []
        for artist in self.album_artists:
            if artist and hasattr(artist, "artist_name"):
                names.append(artist.artist_name)
            else:
                names.append("Unknown Artist")
        return names

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
        return sorted(
            combined, key=lambda x: x.track_number
        )  # 3. Combine them and sort by track number


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
    disc_id = Column(Integer, ForeignKey("discs.disc_id", ondelete="CASCADE"))
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

    # Advanced features
    dynamic_range = Column(Float)  # Range: 6.0 - 20.0+
    stereo_width = Column(Float)  # Range: 0.0 - 1.0
    transient_strength = Column(Float)  # Range: 0.0 - 0.5+
    danceability = Column(Float)  # 0-1 how danceable
    energy = Column(Float)  # 0-1 intensity/activity
    acousticness = Column(Float)  # 0-1 acoustic vs electric
    liveness = Column(Float)  # 0-1 performed live
    valence = Column(Float)  # 0-1 musical positiveness
    fidelity_score = Column(Float)  # 1 - (RMS_compression + spectral_flatness) * 0.5

    album = relationship("Album", back_populates="tracks")
    album_name = association_proxy("album", "album_name")
    album_art = association_proxy("album", "front_cover_path")
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
        """Return comma-separated primary artist names for this track."""
        names = []
        for artist in self.primary_artists:
            if artist and hasattr(artist, "artist_name"):
                names.append(artist.artist_name)
            else:
                names.append("Unknown Artist")
        return ", ".join(names)

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


class Disc(Base):
    __tablename__ = "discs"

    disc_id = Column(Integer, primary_key=True)
    album_id = Column(
        Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), nullable=False
    )
    disc_number = Column(Integer, nullable=False)
    disc_title = Column(String)
    media_type = Column(String)

    album = relationship("Album", backref="discs")
    tracks = relationship(
        "Track",
        backref="disc",
        cascade="all, delete-orphan",
        order_by="Track.track_number",
        viewonly=True,
    )

    @property
    def track_count(self):
        return len(self.tracks)


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
    MBID = Column(String)
    profile_pic_path = Column(String)
    wikipedia_link = Column(String)
    website_link = Column(String)
    artist_type = Column(String)  # Person, Band, Orchestra, Choir

    aliases = relationship(
        "ArtistAlias",
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases_list = association_proxy("aliases", "alias_name")
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


class Genre(Base):
    __tablename__ = "genres"

    genre_id = Column(Integer, primary_key=True)
    genre_name = Column(String)
    description = Column(String)
    parent_id = Column(Integer, ForeignKey("genres.genre_id"))

    parent = relationship("Genre", remote_side=[genre_id], backref="children")
    tracks = relationship("Track", secondary="track_genres", back_populates="genres")
    subgenre_names = association_proxy("children", "genre_name")

    @property
    def track_count(self):
        """Get number of tracks in this genre."""
        return len(self.tracks) if self.tracks else 0

    @property
    def subgenres(self):
        """Get direct subgenres."""
        return self.children

    @property
    def full_genre_path(self):
        """Get full genre hierarchy as string."""
        path = []
        current = self
        while current:
            path.append(current.genre_name)
            current = current.parent
        return " > ".join(reversed(path))

    @property
    def all_subgenres(self):
        """Get all descendant subgenres recursively."""
        result = []
        for child in self.children:
            result.append(child)
            result.extend(child.all_subgenres)
        return result


class Mood(Base):
    __tablename__ = "moods"

    mood_id = Column(Integer, primary_key=True)
    mood_name = Column(String)
    mood_description = Column(String)
    parent_id = Column(Integer, ForeignKey("moods.mood_id"))

    tracks = relationship(
        "Track", secondary="mood_track_association", back_populates="moods"
    )

    @property
    def track_count(self):
        """Get number of tracks with this mood."""
        return len(self.tracks) if self.tracks else 0


class MoodTrackAssociation(Base):
    __tablename__ = "mood_track_association"

    mood_id = Column(
        Integer, ForeignKey("moods.mood_id", ondelete="CASCADE"), primary_key=True
    )
    track_id = Column(
        Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True
    )

    mood = relationship(
        "Mood", backref=backref("mood_tracks", cascade="all, delete-orphan")
    )
    track = relationship(
        "Track", backref=backref("track_moods", cascade="all, delete-orphan")
    )


class Publisher(Base):
    __tablename__ = "publishers"

    publisher_id = Column(Integer, primary_key=True)
    publisher_name = Column(String)
    description = Column(String)
    logo_path = Column(String, unique=True)
    parent_id = Column(Integer, ForeignKey("publishers.publisher_id"))
    begin_year = Column(Integer)
    end_year = Column(Integer)
    is_active = Column(Integer, CheckConstraint("is_active IN (0, 1)"))
    wikipedia_link = Column(String)

    album_associations = relationship(
        "AlbumPublisher",
        back_populates="publisher",
        cascade="all, delete-orphan",
    )
    album_ids = association_proxy("album_associations", "album_id")
    album_names = association_proxy("album_associations", "album.album_name")
    albums = association_proxy("album_associations", "album")


class Place(Base):
    __tablename__ = "places"

    place_id = Column(Integer, primary_key=True)
    place_name = Column(String, nullable=False)
    place_type = Column(String)
    place_latitude = Column(Float)
    place_longitude = Column(Float)
    place_description = Column(String)
    parent_id = Column(Integer, ForeignKey("places.place_id"))

    parent = relationship("Place", remote_side=[place_id], backref="children")
    associations = relationship(
        "PlaceAssociation",
        back_populates="place",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    tracks = relationship(
        "Track",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, "
        "PlaceAssociation.entity_type == 'Track')",
        secondaryjoin="PlaceAssociation.entity_id == Track.track_id",
        back_populates="places",
        overlaps="associations",
    )
    artists = relationship(
        "Artist",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, "
        "PlaceAssociation.entity_type == 'Artist')",
        secondaryjoin="PlaceAssociation.entity_id == Artist.artist_id",
        viewonly=True,
    )

    @property
    def entities(self):
        """Return all entities associated with this place."""
        return [assoc.entity for assoc in self.associations]


class PlaceAssociation(Base):
    __tablename__ = "place_associations"

    association_id = Column(Integer, primary_key=True)
    place_id = Column(Integer, ForeignKey("places.place_id"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_type = Column(
        String,
        CheckConstraint(
            "entity_type IN ('Artist', 'Track', 'Album', 'Publisher', 'Playlist')"
        ),
        nullable=False,
    )
    association_type = Column(String)

    place = relationship("Place", back_populates="associations")

    artist = relationship(
        "Artist",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Artist.artist_id), "
        "PlaceAssociation.entity_type == 'Artist')",
        viewonly=True,
    )
    album = relationship(
        "Album",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Album.album_id), "
        "PlaceAssociation.entity_type == 'Album')",
        viewonly=True,
    )
    track = relationship(
        "Track",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Track.track_id), "
        "PlaceAssociation.entity_type == 'Track')",
        viewonly=True,
    )
    publisher = relationship(
        "Publisher",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Publisher.publisher_id), "
        "PlaceAssociation.entity_type == 'Publisher')",
        viewonly=True,
    )
    playlist = relationship(
        "Playlist",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Playlist.playlist_id), "
        "PlaceAssociation.entity_type == 'Playlist')",
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


class Award(Base):
    __tablename__ = "awards"

    award_id = Column(Integer, primary_key=True)
    award_name = Column(String(100), nullable=False)
    award_year = Column(Integer)
    award_category = Column(String(100))
    award_description = Column(Text)
    wikipedia_link = Column(String)
    parent_id = Column(Integer, ForeignKey("awards.award_id", ondelete="CASCADE"))

    parent = relationship("Award", remote_side=[award_id], backref="children")
    associations = relationship("AwardAssociation", back_populates="award")

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


class Playlist(Base):
    __tablename__ = "playlists"

    playlist_id = Column(Integer, primary_key=True)
    playlist_name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("playlists.playlist_id"))
    playlist_description = Column(String)
    created_date = Column(DateTime, default=datetime.now)
    last_modified = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_smart = Column(Integer, CheckConstraint("is_smart IN (0, 1)"), default=0)

    tracks = relationship(
        "PlaylistTracks",
        back_populates="playlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parent = relationship("Playlist", remote_side=[playlist_id], backref="children")
    smart_playlist = relationship(
        "SmartPlaylist",
        back_populates="playlist",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def track_count(self):
        """Get number of tracks in playlist."""
        return len(self.tracks) if self.tracks else 0

    @property
    def playlist_size(self):
        """get total file size of all tracks in playlist"""
        return sum(track.track.file_size or 0 for track in self.tracks)


class SmartPlaylist(Base):
    __tablename__ = "smart_playlists"

    playlist_id = Column(Integer, ForeignKey("playlists.playlist_id"), primary_key=True)
    last_refreshed = Column(DateTime)
    auto_refresh = Column(Integer, default=0)  # Refresh on app start

    playlist = relationship("Playlist", back_populates="smart_playlist")


class PlaylistTracks(Base):
    __tablename__ = "playlist_tracks"
    __table_args__ = (PrimaryKeyConstraint("playlist_id", "track_id"),)

    playlist_id = Column(
        Integer,
        ForeignKey("playlists.playlist_id", ondelete="CASCADE"),  # Add ondelete
        primary_key=True,
    )
    track_id = Column(
        Integer,
        ForeignKey("tracks.track_id", ondelete="CASCADE"),  # Add ondelete
        primary_key=True,
    )
    position = Column(Integer, nullable=False)
    date_added = Column(DateTime)

    playlist = relationship("Playlist", back_populates="tracks")
    track = relationship("Track", back_populates="playlists")


class SmartPlaylistCriteria(Base):
    __tablename__ = "smart_playlist_criteria"
    criterion_id = Column(Integer, primary_key=True)
    smart_playlist_id = Column(
        Integer, ForeignKey("smart_playlists.playlist_id", ondelete="CASCADE")
    )
    field_name = Column(String)
    comparison = Column(String)
    value = Column(String)
    type = Column(String)


class Role(Base):
    __tablename__ = "roles"

    role_id = Column(Integer, primary_key=True)
    role_name = Column(String)
    role_description = Column(String)
    role_type = Column(String)
    parent_id = Column(Integer, ForeignKey("roles.role_id"))
    _artist_count = Column(Integer, default=0)

    # Relationships
    parent = relationship("Role", remote_side=[role_id], backref="children")
    track_roles = relationship(
        "TrackArtistRole",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def artist_count(self):
        """Get the artist count, either from cache or calculate it."""
        if self._artist_count > 0:
            return self._artist_count
        return len({tr.artist_id for tr in self.track_roles})


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

    association_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"))
    artist_id = Column(Integer, ForeignKey("artists.artist_id", ondelete="CASCADE"))
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"))

    # Relationships
    album = relationship("Album", back_populates="album_roles")
    artist = relationship("Artist", back_populates="album_roles")
    role = relationship("Role", back_populates="album_roles")


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


# Indexes
Index("idx_artists_name", Artist.artist_name)
Index("idx_albums_title", Album.album_name)
Index("idx_tracks_path", Track.track_file_path)
Index("idx_tracks_title", Track.track_name)
Index("idx_genres_name", Genre.genre_name)
Index("ix_album_publisher_unique", "album_id", "publisher_id", unique=True)
Index("idx_artist_begin_end", Artist.begin_year, Artist.end_year)
Index("idx_track_artist_roles", TrackArtistRole.artist_id, TrackArtistRole.track_id)
Index("idx_album_roles", AlbumRoleAssociation.album_id, AlbumRoleAssociation.artist_id)
Index("idx_tracks_disc_id", Track.disc_id)
Index("idx_discs_album_number", Disc.album_id, Disc.disc_number)
Index("idx_track_album_id", Track.album_id)
Index("idx_track_disc_id", Track.disc_id)
Index("idx_album_publisher_publisher_id", AlbumPublisher.publisher_id)
Index(
    "idx_mood_track_association",
    MoodTrackAssociation.mood_id,
    MoodTrackAssociation.track_id,
)
Index("idx_place_associations", PlaceAssociation.place_id, PlaceAssociation.entity_id)
Index("idx_award_associations", AwardAssociation.award_id, AwardAssociation.entity_id)
Index("idx_track_genres", TrackGenre.track_id, TrackGenre.genre_id)


class MusicDatabase:
    def __init__(self, db_path: str = "sqlite:///music_library.db") -> None:
        try:
            self.engine = create_engine(db_path, echo=False)
            self.Session = sessionmaker(bind=self.engine)
            self._initialize_database()
            self._verify_integrity()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _initialize_database(self):
        """Creates the database schema if it doesn't already exist."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def _verify_integrity(self):
        try:
            expected_tables = {
                "albums",
                "tracks",
                "artists",
                "genres",
                "moods",
                "publishers",
                "places",
                "place_associations",
                "group_membership",
                "track_artist_roles",
                "playlists",
                "awards",
                "album_role_association",
                "playlist_tracks",
                "discs",
                "roles",
                "album_publisher",
                "track_genres",
                "mood_track_association",
                "award_associations",
                "artist_influences",
                "smart_playlists",
                "smart_playlist_criteria",
                "artist_alias",
                "samples",
                "album_virtual_tracks",
            }

            inspector = inspect(self.engine)
            existing_tables = set(inspector.get_table_names())

            missing_tables = expected_tables - existing_tables
            if missing_tables:
                logger.warning(f"Missing tables: {missing_tables}. Recreating...")
                Base.metadata.create_all(self.engine)
                logger.info("Database schema recreated successfully.")
            else:
                logger.info("Database integrity check passed.")

        except Exception as e:
            logger.error(f"Integrity check failed: {e}")
            raise
