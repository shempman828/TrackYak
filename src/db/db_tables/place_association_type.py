"""
PlaceAssociationType ORM model: canonical list of ways a Place can be
associated with an entity (Birthplace, Origin, Recording Location, ...).
"""

from sqlalchemy import Column, Integer, String

from src.db.db_tables.base import Base


class PlaceAssociationType(Base):
    __tablename__ = "place_association_types"

    association_type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=False, unique=True)
    type_description = Column(String)
