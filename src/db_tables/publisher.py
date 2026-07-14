"""
Publisher ORM model.
"""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db_tables.base import Base


class Publisher(Base):
    __tablename__ = "publishers"

    publisher_id = Column(Integer, primary_key=True)
    publisher_name = Column(String)
    description = Column(String)
    logo_path = Column(String, unique=True)
    parent_id = Column(Integer, ForeignKey("publishers.publisher_id"))
    begin_year = Column(Integer)
    end_year = Column(Integer)
    is_active = Column(Integer, CheckConstraint("is_active IN (0, 1)"))
    wikipedia_link = Column(String)

    album_associations = relationship(
        "AlbumPublisher",
        back_populates="publisher",
        cascade="all, delete-orphan",
    )
    album_ids = association_proxy("album_associations", "album_id")
    album_names = association_proxy("album_associations", "album.album_name")
    albums = association_proxy("album_associations", "album")
