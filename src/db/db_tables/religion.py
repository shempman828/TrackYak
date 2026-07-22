"""
Religion ORM model (self-referential hierarchy), e.g. Christianity > Catholicism.
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Religion(Base):
    __tablename__ = "religions"

    religion_id = Column(Integer, primary_key=True)
    religion_name = Column(String, nullable=False, unique=True)
    description = Column(String)
    parent_id = Column(Integer, ForeignKey("religions.religion_id", ondelete="SET NULL"))

    parent = relationship("Religion", remote_side=[religion_id], backref="children")
    artists = relationship("Artist", back_populates="religion")

    @property
    def full_religion_path(self):
        """Full hierarchy as a string, e.g. 'Christianity > Catholicism'."""
        path = []
        current = self
        while current:
            path.append(current.religion_name)
            current = current.parent
        return " > ".join(reversed(path))
