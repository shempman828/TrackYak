"""
Role ORM model (self-referential hierarchy of artist/album credit roles).
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Role(Base):
    __tablename__ = "roles"

    role_id = Column(Integer, primary_key=True)
    role_name = Column(String)
    role_description = Column(String)
    role_type = Column(String)
    parent_id = Column(Integer, ForeignKey("roles.role_id"))
    _artist_count = Column(Integer, default=0)

    # Relationships
    parent = relationship("Role", remote_side=[role_id], backref="children")
    track_roles = relationship(
        "TrackArtistRole",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def artist_count(self):
        """Get the artist count, either from cache or calculate it."""
        if self._artist_count > 0:
            return self._artist_count
        return len({tr.artist_id for tr in self.track_roles})
