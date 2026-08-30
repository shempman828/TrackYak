"""
Role ORM model (self-referential hierarchy of artist/album credit roles).
"""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.associationproxy import association_proxy
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
        "TrackArtistRole", back_populates="role", cascade="all, delete-orphan", passive_deletes=True
    )
    album_roles = relationship(
        "AlbumRoleAssociation",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases = relationship(
        "RoleAlias", back_populates="role", cascade="all, delete-orphan", passive_deletes=True
    )
    aliases_list = association_proxy("aliases", "alias_name")

    @property
    def artist_count(self):
        """Get the artist count, either from cache or calculate it."""
        if self._artist_count > 0:
            return self._artist_count
        return len({tr.artist_id for tr in self.track_roles})


class RoleAlias(Base):
    """An alternate name for a role that always resolves to it.

    Merging a duplicate role into a canonical one automatically adds the
    merged-away name as an alias (see MergeDB.merge_entities), and
    importing checks aliases before creating a new role, so the same
    "duplicate" name doesn't keep coming back on every import. Mirrors
    GenreAlias/ArtistAlias/PublisherAlias exactly.
    """

    __tablename__ = "role_alias"

    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)

    role = relationship("Role", back_populates="aliases")
    role_name = association_proxy("role", "role_name")


class RoleSplitAlias(Base):
    """A recorded 'this combined name splits into these roles' rule.

    Unlike RoleAlias (name -> one role), one alias_name here maps to
    *multiple* Role rows, ordered by sort_order. Created automatically
    when a Role is split into 2+ roles (see SplitDB.split_role), or
    directly via the alias-management dialog.
    """

    __tablename__ = "role_split_alias"

    split_alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    role = relationship("Role")

    __table_args__ = (
        UniqueConstraint("alias_name", "role_id", name="uq_role_split_alias_name_role"),
    )
