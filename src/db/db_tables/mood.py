"""
Mood ORM models: Mood and its track association table.
"""

from sqlalchemy import Column, ForeignKey, Integer, String
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
        "Track",
        secondary="mood_track_association",
        back_populates="moods",
        viewonly=True,
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
