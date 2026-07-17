"""
ArtistType ORM model: canonical role classification(s) an artist is best
known for (Composer, Producer, Pianist, ...). Independent of the per-credit
Role system (TrackArtistRole/AlbumRoleAssociation) -- an incidental credit on
one track doesn't change an artist's canonical type(s).
"""

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class ArtistType(Base):
    __tablename__ = "artist_types"

    artist_type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=False, unique=True)
    type_description = Column(String)

    artists = relationship(
        "Artist", secondary="artist_type_associations", back_populates="types"
    )
