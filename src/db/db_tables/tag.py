"""
Tag ORM models: TagType (flat, user-defined category) and Tag (self-referential
hierarchy within a TagType), e.g. TagType "Religion" holding
Christianity > Catholicism.
"""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class TagType(Base):
    __tablename__ = "tag_types"

    tag_type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=False, unique=True)
    description = Column(String)
    sort_order = Column(Integer, nullable=False, default=0)

    tags = relationship(
        "Tag", back_populates="tag_type", cascade="all, delete-orphan", passive_deletes=True
    )


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("tag_type_id", "tag_name", name="uq_tag_type_name"),)

    tag_id = Column(Integer, primary_key=True)
    tag_name = Column(String, nullable=False)
    description = Column(String)
    parent_id = Column(Integer, ForeignKey("tags.tag_id", ondelete="SET NULL"))
    tag_type_id = Column(
        Integer, ForeignKey("tag_types.tag_type_id", ondelete="CASCADE"), nullable=False
    )

    parent = relationship("Tag", remote_side=[tag_id], backref="children")
    tag_type = relationship("TagType", back_populates="tags")
    artists = relationship("Artist", secondary="artist_tag_associations", back_populates="tags")

    @property
    def full_tag_path(self):
        """Full hierarchy as a string, e.g. 'Christianity > Catholicism'."""
        path = []
        current = self
        while current:
            path.append(current.tag_name)
            current = current.parent
        return " > ".join(reversed(path))
