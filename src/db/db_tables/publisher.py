"""
Publisher ORM model.
"""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


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
    is_fixed = Column(Integer)

    album_associations = relationship(
        "AlbumPublisher",
        back_populates="publisher",
        cascade="all, delete-orphan",
    )
    album_ids = association_proxy("album_associations", "album_id")
    album_names = association_proxy("album_associations", "album.album_name")
    albums = association_proxy("album_associations", "album")

    aliases = relationship(
        "PublisherAlias",
        back_populates="publisher",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases_list = association_proxy("aliases", "alias_name")


class PublisherAlias(Base):
    """An alternate name for a publisher that always resolves to it.

    Merging a duplicate publisher into a canonical one automatically adds
    the merged-away name as an alias (see MergeDB.merge_entities), and
    importing checks aliases before creating a new publisher, so the same
    "duplicate" name doesn't keep coming back on every import.
    """

    __tablename__ = "publisher_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    publisher_id = Column(
        Integer, ForeignKey("publishers.publisher_id", ondelete="CASCADE"), nullable=False
    )

    publisher = relationship("Publisher", back_populates="aliases")
    publisher_name = association_proxy("publisher", "publisher_name")
