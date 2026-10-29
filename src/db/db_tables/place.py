"""Place ORM models: Place (self-referential hierarchy) and its polymorphic association table."""

from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import ENTITY_RELATIONSHIP_ATTRS, Base


class Place(Base):
    """Geographic place (country, city, venue, ...), nested under a parent place."""

    __tablename__ = "places"

    place_id = Column(Integer, primary_key=True)
    place_name = Column(String, nullable=False)
    place_type = Column(String)
    place_latitude = Column(Float)
    place_longitude = Column(Float)
    place_description = Column(String)
    parent_id = Column(Integer, ForeignKey("places.place_id", ondelete="SET NULL"))
    MBID = Column(String)

    parent = relationship("Place", remote_side=[place_id], backref="children")
    # No passive_deletes: older DB files have no ON DELETE on place_id, so the
    # ORM must load and delete these rows itself.
    associations = relationship("PlaceAssociation", back_populates="place", cascade="all, delete-orphan")

    # viewonly: writes to place_associations always go through PlaceAssociation
    # objects directly, never through this collection (see associations above).
    tracks = relationship(
        "Track",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, PlaceAssociation.entity_type == 'Track')",
        secondaryjoin="PlaceAssociation.entity_id == Track.track_id",
        back_populates="places",
        viewonly=True,
    )
    artists = relationship(
        "Artist",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, PlaceAssociation.entity_type == 'Artist')",
        secondaryjoin="PlaceAssociation.entity_id == Artist.artist_id",
        viewonly=True,
    )

    @property
    def entities(self):
        """All entities associated with this place, skipping broken links."""
        return [entity for assoc in self.associations if (entity := assoc.entity) is not None]

    @property
    def association_count(self):
        """Number of entities directly associated with this place."""
        return len(self.associations)

    def _walk_tree(self):
        """Yield this place and every descendant once, stopping at a cycle."""
        visited = set()
        stack = [self]
        while stack:
            place = stack.pop()
            if place.place_id in visited:
                continue
            visited.add(place.place_id)
            yield place
            stack.extend(place.children)

    @property
    def recursive_association_count(self):
        """Number of associations for this place and all descendants."""
        return len({assoc.association_id for place in self._walk_tree() for assoc in place.associations})

    def recursive_rated_tracks(self, rating_min, rating_max):
        """(track_id, user_rating) pairs for in-range rated tracks of this place and all descendants."""
        seen_track_ids = set()
        ratings = []
        for place in self._walk_tree():
            for track in place.tracks:
                if track.track_id in seen_track_ids:
                    continue
                seen_track_ids.add(track.track_id)
                rating = track.user_rating
                if rating is not None and rating_min <= rating <= rating_max:
                    ratings.append((track.track_id, rating))
        return ratings

    @property
    def recursive_artist_ids(self):
        """Artist ids associated with this place or any descendant place."""
        return {assoc.entity_id for place in self._walk_tree() for assoc in place.associations if assoc.entity_type == "Artist"}


class PlaceAssociation(Base):
    """Polymorphic link from a place to an Artist, Track, Album, Publisher, or Playlist."""

    __tablename__ = "place_associations"

    association_id = Column(Integer, primary_key=True)
    place_id = Column(Integer, ForeignKey("places.place_id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_type = Column(String, CheckConstraint("entity_type IN ('Artist', 'Track', 'Album', 'Publisher', 'Playlist')"), nullable=False)
    association_type_id = Column(Integer, ForeignKey("place_association_types.association_type_id", ondelete="SET NULL"))

    place = relationship("Place", back_populates="associations")
    association_type = relationship("PlaceAssociationType")

    # uselist=False: foreign() on the remote side otherwise makes each of these a list.
    artist = relationship("Artist", primaryjoin="and_(PlaceAssociation.entity_id == foreign(Artist.artist_id), PlaceAssociation.entity_type == 'Artist')", uselist=False, viewonly=True)
    album = relationship("Album", primaryjoin="and_(PlaceAssociation.entity_id == foreign(Album.album_id), PlaceAssociation.entity_type == 'Album')", uselist=False, viewonly=True)
    track = relationship("Track", primaryjoin="and_(PlaceAssociation.entity_id == foreign(Track.track_id), PlaceAssociation.entity_type == 'Track')", uselist=False, viewonly=True)
    publisher = relationship("Publisher", primaryjoin="and_(PlaceAssociation.entity_id == foreign(Publisher.publisher_id), PlaceAssociation.entity_type == 'Publisher')", uselist=False, viewonly=True)
    playlist = relationship("Playlist", primaryjoin="and_(PlaceAssociation.entity_id == foreign(Playlist.playlist_id), PlaceAssociation.entity_type == 'Playlist')", uselist=False, viewonly=True)

    @property
    def entity(self):
        """The linked entity object, loading only the relationship for entity_type."""
        attr = ENTITY_RELATIONSHIP_ATTRS.get(self.entity_type)
        return getattr(self, attr) if attr else None
