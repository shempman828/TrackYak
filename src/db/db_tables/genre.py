"""Genre ORM models: Genre (self-referential hierarchy) and its aliases."""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base, walk_parents


class Genre(Base):
    """Track genre, nested under a parent genre."""

    __tablename__ = "genres"

    genre_id = Column(Integer, primary_key=True)
    genre_name = Column(String)
    description = Column(String)
    parent_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="SET NULL"))

    parent = relationship("Genre", remote_side=[genre_id], backref="children")
    tracks = relationship("Track", secondary="track_genres", back_populates="genres")
    subgenre_names = association_proxy("children", "genre_name")

    aliases = relationship("GenreAlias", back_populates="genre", cascade="all, delete-orphan", passive_deletes=True)
    aliases_list = association_proxy("aliases", "alias_name")

    @property
    def track_count(self):
        """Number of tracks in this genre."""
        return len(self.tracks)

    @property
    def depth(self):
        """Nesting depth (0 for a root genre with no parent)."""
        return sum(1 for _ in walk_parents(self)) - 1

    @property
    def subgenres(self):
        """Direct subgenres."""
        return self.children

    @property
    def full_genre_path(self):
        """Full genre hierarchy as a string, e.g. 'Rock > Punk'."""
        return " > ".join(reversed([g.genre_name for g in walk_parents(self)]))

    @property
    def all_subgenres(self):
        """All descendant subgenres, depth-first."""
        result = []
        seen = {id(self)}  # guards against a parent cycle

        def walk(genre):
            for child in genre.children:
                if id(child) in seen:
                    continue
                seen.add(id(child))
                result.append(child)
                walk(child)

        walk(self)
        return result

    @property
    def all_track_count(self):
        """Track count of this genre plus all sub-genres (a track in two genres counts twice)."""
        return self.track_count + sum(subgenre.track_count for subgenre in self.all_subgenres)


class GenreAlias(Base):
    """Alternate name that always resolves to one genre (see GetFromDB.resolve_entity_or_alias)."""

    __tablename__ = "genre_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    genre_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), nullable=False)

    genre = relationship("Genre", back_populates="aliases")
    genre_name = association_proxy("genre", "genre_name")


class GenreSplitAlias(Base):
    """Rule that one combined name splits into several genres, ordered by sort_order."""

    __tablename__ = "genre_split_alias"

    split_alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, nullable=False, index=True)
    genre_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    genre = relationship("Genre")

    __table_args__ = (UniqueConstraint("alias_name", "genre_id", name="uq_genre_split_alias_name_genre"),)
