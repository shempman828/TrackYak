"""
Mood ORM models: Mood and its track association table.
"""

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import backref, relationship

from src.db.db_tables.base import Base


class Mood(Base):
    __tablename__ = "moods"

    mood_id = Column(Integer, primary_key=True)
    mood_name = Column(String)
    mood_description = Column(String)
    parent_id = Column(Integer, ForeignKey("moods.mood_id"))
    parent = relationship("Mood", remote_side=[mood_id], backref="children")

    # viewonly: writes to mood_track_association always go through
    # MoodTrackAssociation objects directly, never through this collection.
    tracks = relationship(
        "Track", secondary="mood_track_association", back_populates="moods", viewonly=True
    )

    @property
    def track_count(self):
        """Get number of tracks with this mood."""
        return len(self.tracks) if self.tracks else 0

    @property
    def mood_size(self):
        """get total file size of all tracks with this mood"""
        return sum(track.file_size or 0 for track in self.tracks)


class MoodTrackAssociation(Base):
    __tablename__ = "mood_track_association"

    mood_id = Column(Integer, ForeignKey("moods.mood_id", ondelete="CASCADE"), primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)

    # Lyrics-match strength for THIS (mood, track) pair: the mood's keyword
    # `density` (raw hits / total lyric tokens) as computed by
    # mood_scoring.score_moods_detailed() at auto-tag time. Powers the
    # "most representative tracks per mood" statistic
    # (docs/specs/mood_representative_tracks.md).
    #   NULL  -> never scored (manual tag predating this column, or a
    #            split-created row); healed by the startup backfill.
    #   0.0   -> scored, this mood's keywords don't match the track's lyrics.
    #   > 0   -> match density; higher = more representative of the mood.
    # Written only when the row is first created -- editing lyrics later
    # does not refresh an existing row's score (additive-only mood system).
    score = Column(Float)

    mood = relationship("Mood", backref=backref("mood_tracks", cascade="all, delete-orphan"))
    track = relationship("Track", backref=backref("track_moods", cascade="all, delete-orphan"))
