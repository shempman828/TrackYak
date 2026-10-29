"""Publisher ORM models: Publisher (self-referential hierarchy) and its aliases."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Publisher(Base):
    """Record label or publisher, nested under a parent company."""

    __tablename__ = "publishers"

    publisher_id = Column(Integer, primary_key=True)
    publisher_name = Column(String)
    description = Column(String)
    parent_id = Column(Integer, ForeignKey("publishers.publisher_id", ondelete="SET NULL"))
    parent = relationship("Publisher", remote_side=[publisher_id], backref="children")
    begin_year = Column(Integer)
    begin_month = Column(Integer)
    begin_day = Column(Integer)
    end_year = Column(Integer)
    end_month = Column(Integer)
    end_day = Column(Integer)
    is_active = Column(Integer, CheckConstraint("is_active IN (0, 1)"))
    wikipedia_link = Column(String)
    first_pass = Column(Integer, CheckConstraint("first_pass IN (0, 1)"))
    second_pass = Column(Integer, CheckConstraint("second_pass IN (0, 1)"))
    MBID = Column(String)

    album_associations = relationship("AlbumPublisher", back_populates="publisher", cascade="all, delete-orphan")
    album_ids = association_proxy("album_associations", "album_id")
    album_names = association_proxy("album_associations", "album.album_name")
    albums = association_proxy("album_associations", "album")

    aliases = relationship("PublisherAlias", back_populates="publisher", cascade="all, delete-orphan", passive_deletes=True)
    aliases_list = association_proxy("aliases", "alias_name")

    founder_associations = relationship("PublisherFounder", back_populates="publisher", cascade="all, delete-orphan", passive_deletes=True)
    founders = association_proxy("founder_associations", "artist")


class PublisherAlias(Base):
    """Alternate name that always resolves to one publisher (see MergeDB.merge_entities)."""

    __tablename__ = "publisher_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    publisher_id = Column(Integer, ForeignKey("publishers.publisher_id", ondelete="CASCADE"), nullable=False)

    publisher = relationship("Publisher", back_populates="aliases")
    publisher_name = association_proxy("publisher", "publisher_name")


class PublisherSplitAlias(Base):
    """Rule that one combined name splits into several publishers, ordered by sort_order."""

    __tablename__ = "publisher_split_alias"

    split_alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, nullable=False, index=True)
    publisher_id = Column(Integer, ForeignKey("publishers.publisher_id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    publisher = relationship("Publisher")

    __table_args__ = (UniqueConstraint("alias_name", "publisher_id", name="uq_publisher_split_alias_name_publisher"),)
