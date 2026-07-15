"""
Disc ORM model.
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


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
        order_by="Track.track_number",
        viewonly=True,
    )

    @property
    def track_count(self):
        return len(self.tracks)
