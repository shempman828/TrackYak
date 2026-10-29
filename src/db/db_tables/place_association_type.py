"""PlaceAssociationType ORM model."""

from sqlalchemy import Column, Integer, String

from src.db.db_tables.base import Base


class PlaceAssociationType(Base):
    """Canonical way a Place links to an entity (Birthplace, Origin, Recording Location, ...)."""

    __tablename__ = "place_association_types"

    association_type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=False, unique=True)
    type_description = Column(String)
