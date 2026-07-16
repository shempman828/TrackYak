"""
Place ORM models: Place (self-referential hierarchy) and its polymorphic association table.
"""

from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Place(Base):
    __tablename__ = "places"

    place_id = Column(Integer, primary_key=True)
    place_name = Column(String, nullable=False)
    place_type = Column(String)
    place_latitude = Column(Float)
    place_longitude = Column(Float)
    place_description = Column(String)
    parent_id = Column(Integer, ForeignKey("places.place_id"))

    parent = relationship("Place", remote_side=[place_id], backref="children")
    associations = relationship(
        "PlaceAssociation",
        back_populates="place",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    tracks = relationship(
        "Track",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, "
        "PlaceAssociation.entity_type == 'Track')",
        secondaryjoin="PlaceAssociation.entity_id == Track.track_id",
        back_populates="places",
        overlaps="associations",
    )
    artists = relationship(
        "Artist",
        secondary="place_associations",
        primaryjoin="and_(Place.place_id == PlaceAssociation.place_id, "
        "PlaceAssociation.entity_type == 'Artist')",
        secondaryjoin="PlaceAssociation.entity_id == Artist.artist_id",
        viewonly=True,
    )

    @property
    def entities(self):
        """Return all entities associated with this place."""
        return [assoc.entity for assoc in self.associations]

    @property
    def association_count(self):
        """Return the number of entities directly associated with this place."""
        return len(self.associations)

    @property
    def recursive_association_count(self):
        """Return the number of associations for this place and all descendants."""
        visited_places = set()
        association_ids = set()

        def walk(place):
            if place.place_id in visited_places:
                return
            visited_places.add(place.place_id)

            for assoc in place.associations:
                association_ids.add(assoc.association_id)

            for child in place.children:
                walk(child)

        walk(self)
        return len(association_ids)


class PlaceAssociation(Base):
    __tablename__ = "place_associations"

    association_id = Column(Integer, primary_key=True)
    place_id = Column(Integer, ForeignKey("places.place_id"), nullable=False)
    entity_id = Column(Integer, nullable=False)
    entity_type = Column(
        String,
        CheckConstraint(
            "entity_type IN ('Artist', 'Track', 'Album', 'Publisher', 'Playlist')"
        ),
        nullable=False,
    )
    association_type_id = Column(
        Integer,
        ForeignKey("place_association_types.association_type_id", ondelete="SET NULL"),
    )

    place = relationship("Place", back_populates="associations")
    association_type = relationship("PlaceAssociationType")

    artist = relationship(
        "Artist",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Artist.artist_id), "
        "PlaceAssociation.entity_type == 'Artist')",
        viewonly=True,
    )
    album = relationship(
        "Album",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Album.album_id), "
        "PlaceAssociation.entity_type == 'Album')",
        viewonly=True,
    )
    track = relationship(
        "Track",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Track.track_id), "
        "PlaceAssociation.entity_type == 'Track')",
        viewonly=True,
    )
    publisher = relationship(
        "Publisher",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Publisher.publisher_id), "
        "PlaceAssociation.entity_type == 'Publisher')",
        viewonly=True,
    )
    playlist = relationship(
        "Playlist",
        primaryjoin="and_(PlaceAssociation.entity_id == foreign(Playlist.playlist_id), "
        "PlaceAssociation.entity_type == 'Playlist')",
        viewonly=True,
    )

    @property
    def entity(self):
        """Return the actual entity object dynamically."""
        entity_getters = {
            "Artist": self.artist,
            "Album": self.album,
            "Track": self.track,
            "Publisher": self.publisher,
            "Playlist": self.playlist,
        }
        return entity_getters.get(self.entity_type)
