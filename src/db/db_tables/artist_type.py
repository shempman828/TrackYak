"""ArtistType ORM model: canonical classification(s) an artist is best known for."""

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class ArtistType(Base):
    """Canonical artist type (Composer, Pianist, ...), independent of per-credit Roles."""

    __tablename__ = "artist_types"

    artist_type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=False, unique=True)
    type_description = Column(String)

    artists = relationship("Artist", secondary="artist_type_associations", back_populates="types")
