"""Disc ORM model."""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Disc(Base):
    """One disc (medium) of an album."""

    __tablename__ = "discs"

    disc_id = Column(Integer, primary_key=True)
    album_id = Column(Integer, ForeignKey("albums.album_id", ondelete="CASCADE"), nullable=False)
    disc_number = Column(Integer, nullable=False)
    disc_title = Column(String)

    album = relationship("Album", back_populates="discs")
    # viewonly backref: assigning Track.disc does not persist -- set Track.disc_id.
    tracks = relationship("Track", backref="disc", order_by="Track.track_number", viewonly=True)

    @property
    def track_count(self):
        """Number of tracks on this disc."""
        return len(self.tracks)
