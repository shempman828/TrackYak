"""Mood ORM models: Mood and its track association table."""

from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import backref, relationship

from src.db.db_tables.base import Base


class Mood(Base):
    """Track mood, in a self-referential hierarchy."""

    __tablename__ = "moods"

    mood_id = Column(Integer, primary_key=True)
    mood_name = Column(String)
    mood_description = Column(String)
    parent_id = Column(Integer, ForeignKey("moods.mood_id", ondelete="SET NULL"))
    parent = relationship("Mood", remote_side=[mood_id], backref="children")

    # viewonly: writes to mood_track_association always go through
    # MoodTrackAssociation objects directly, never through this collection.
    tracks = relationship("Track", secondary="mood_track_association", back_populates="moods", viewonly=True)

    @property
    def track_count(self):
        """Number of tracks with this mood."""
        return len(self.tracks)

    @property
    def mood_size(self):
        """Total file size of all tracks with this mood."""
        return sum(track.file_size or 0 for track in self.tracks)


class MoodTrackAssociation(Base):
    """Link between a mood and a track, with its lyrics-match score."""

    __tablename__ = "mood_track_association"

    mood_id = Column(Integer, ForeignKey("moods.mood_id", ondelete="CASCADE"), primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)

    # Keyword density from mood_scoring.score_moods_detailed() at auto-tag time
    # (docs/specs/mood_representative_tracks.md). NULL = never scored (healed by
    # the startup backfill); 0.0 = no match; > 0 = higher is more representative.
    # Written once on row creation -- later lyric edits do not refresh it.
    score = Column(Float)

    mood = relationship("Mood", backref=backref("mood_tracks", cascade="all, delete-orphan"))
    track = relationship("Track", backref=backref("track_moods", cascade="all, delete-orphan"))
