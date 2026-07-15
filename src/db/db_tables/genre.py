"""
Genre ORM model (self-referential hierarchy).
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


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

    @property
    def all_track_count(self):
        """Get total track count including all sub-genres recursively."""
        total = self.track_count
        for subgenre in self.all_subgenres:
            total += subgenre.track_count
        return total
