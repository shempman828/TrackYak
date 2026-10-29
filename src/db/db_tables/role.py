"""Role ORM models: Role (self-referential hierarchy of credit roles) and its aliases."""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Role(Base):
    """Artist/album credit role, e.g. "Album Artist" or "Producer"."""

    __tablename__ = "roles"

    role_id = Column(Integer, primary_key=True)
    role_name = Column(String)
    role_description = Column(String)
    role_type = Column(String)
    parent_id = Column(Integer, ForeignKey("roles.role_id", ondelete="SET NULL"))
    _artist_count = Column(Integer, default=0)

    parent = relationship("Role", remote_side=[role_id], backref="children")
    track_roles = relationship("TrackArtistRole", back_populates="role", cascade="all, delete-orphan", passive_deletes=True)
    album_roles = relationship("AlbumRoleAssociation", back_populates="role", cascade="all, delete-orphan", passive_deletes=True)
    aliases = relationship("RoleAlias", back_populates="role", cascade="all, delete-orphan", passive_deletes=True)
    aliases_list = association_proxy("aliases", "alias_name")

    @property
    def artist_count(self):
        """Cached artist count, or the distinct credited artists when no cache is set."""
        if self._artist_count:  # NULL on legacy rows
            return self._artist_count
        return len({tr.artist_id for tr in self.track_roles})


class RoleAlias(Base):
    """Alternate name that always resolves to one role (see MergeDB.merge_entities)."""

    __tablename__ = "role_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)

    role = relationship("Role", back_populates="aliases")
    role_name = association_proxy("role", "role_name")


class RoleSplitAlias(Base):
    """Rule that one combined name splits into several roles, ordered by sort_order."""

    __tablename__ = "role_split_alias"

    split_alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    role = relationship("Role")

    __table_args__ = (UniqueConstraint("alias_name", "role_id", name="uq_role_split_alias_name_role"),)
