"""Regression test for bug #252: merging artists could silently drop the
losing artist's album_role_association rows instead of re-pointing them to
the surviving artist.

MergeDB.merge_entities() migrates FK rows with a raw SQL UPDATE, then does
`session_base.delete(source_entity)` so ORM cascade rules apply to any tables it
didn't explicitly touch. But Artist.album_roles is
`cascade="all, delete-orphan"` with `passive_deletes=True`, which only skips
*loading* the collection on delete -- it does not stop cascade-delete from
acting on a collection that was already loaded earlier in the session_base (e.g.
by viewing the artist's detail panel, which reads `artist.albums` ->
`artist.album_roles`). In that case cascade-delete walked the stale
in-memory list and deleted the just-migrated rows by primary key instead of
leaving them re-pointed to the target artist.

Covers MergeDB.merge_entities (src/db/db_helpers/merge.py) against a real
in-memory SQLite session_base so the ORM cascade path actually executes.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.role import Role, RoleAlias

# ---- test_merge__self_base.py ------------------------------------------------
@pytest.fixture
def session_base():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_base = Session()
    yield session_base
    session_base.close()

def _make_artists_album_and_role(session_base):
    source = Artist(artist_name="Source Artist")
    target = Artist(artist_name="Target Artist")
    album = Album(album_name="Some Album")
    role = Role(role_name="Album Artist")
    session_base.add_all([source, target, album, role])
    session_base.commit()

    session_base.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=source.artist_id, role_id=role.role_id
        )
    )
    session_base.commit()
    return source, target, album, role

def test_merge_preserves_album_role_when_collection_preloaded(session_base):
    source, target, album, role = _make_artists_album_and_role(session_base)

    # Simulate viewing the source artist's detail panel before merging,
    # which loads `artist.album_roles` into the session_base's identity map.
    assert len(source.album_roles) == 1

    merger = MergeDB(session_base)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = (
        session_base.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
    )
    assert len(remaining) == 1
    assert remaining[0].artist_id == target.artist_id

def test_merge_preserves_album_role_when_collection_not_preloaded(session_base):
    source, target, album, role = _make_artists_album_and_role(session_base)

    merger = MergeDB(session_base)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = (
        session_base.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
    )
    assert len(remaining) == 1
    assert remaining[0].artist_id == target.artist_id

# ---- test_merge_place.py -----------------------------------------------------
# Regression tests for place merging (MergeDB.merge_entities("Place", ...)).
#
# Place has two quirks the generic FK-scanning merge loop doesn't handle on
# its own:
#
# 1. `places.parent_id` is a self-referential FK, so the generic loop (which
#    skips the entity's own table) never migrates it. Left alone, a source
#    place's children would fall through to the ORM's default delete-time
#    behavior of nulling their parent_id, silently detaching them from the
#    hierarchy instead of being reparented onto the target.
# 2. `place_associations` has no unique constraint, so the usual
#    IntegrityError-triggered "drop duplicate rows" fallback never fires for
#    it -- duplicate associations must be dropped explicitly.
#
# Covers MergeDB.merge_entities (src/db/db_helpers/merge.py) against a real
# in-memory SQLite session_pl so the ORM cascade path actually executes.
@pytest.fixture
def session_pl():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_pl = Session()
    yield session_pl
    session_pl.close()

def test_merge_reparents_children_onto_target_pl(session_pl):
    source = Place(place_name="Source Region")
    target = Place(place_name="Target Region")
    session_pl.add_all([source, target])
    session_pl.commit()

    child1 = Place(place_name="Child One", parent_id=source.place_id)
    child2 = Place(place_name="Child Two", parent_id=source.place_id)
    session_pl.add_all([child1, child2])
    session_pl.commit()
    child1_id, child2_id, target_id = child1.place_id, child2.place_id, target.place_id

    merger = MergeDB(session_pl)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session_pl.expire_all()
    assert session_pl.get(Place, child1_id).parent_id == target_id
    assert session_pl.get(Place, child2_id).parent_id == target_id
    assert session_pl.get(Place, source.place_id) is None

def test_merge_promotes_branch_when_target_is_descendant_of_source_pl(session_pl):
    """Merging a place into its own grandchild must not create a cycle."""
    source = Place(place_name="Country")
    session_pl.add(source)
    session_pl.commit()

    mid = Place(place_name="State", parent_id=source.place_id)
    sibling = Place(place_name="Other State", parent_id=source.place_id)
    session_pl.add_all([mid, sibling])
    session_pl.commit()

    target = Place(place_name="City", parent_id=mid.place_id)
    session_pl.add(target)
    session_pl.commit()

    source_id, mid_id, sibling_id, target_id = (
        source.place_id,
        mid.place_id,
        sibling.place_id,
        target.place_id,
    )

    merger = MergeDB(session_pl)
    result = merger.merge_entities("Place", source_id, target_id)
    assert result is True

    session_pl.expire_all()
    # The branch leading to target (mid) is promoted to source's old
    # position instead of being pointed at target, which would have been
    # a two-node cycle (mid -> target -> mid).
    assert session_pl.get(Place, mid_id).parent_id is None
    # Unrelated children of source reparent onto target normally.
    assert session_pl.get(Place, sibling_id).parent_id == target_id
    # Target itself is untouched -- still a child of mid, no self-loop.
    assert session_pl.get(Place, target_id).parent_id == mid_id
    assert session_pl.get(Place, source_id) is None

def test_merge_promotes_target_when_target_is_direct_child_of_source(session_pl):
    source = Place(place_name="Parent Place")
    session_pl.add(source)
    session_pl.commit()

    target = Place(place_name="Child Place", parent_id=source.place_id)
    sibling = Place(place_name="Sibling Place", parent_id=source.place_id)
    session_pl.add_all([target, sibling])
    session_pl.commit()
    target_id, sibling_id = target.place_id, sibling.place_id

    merger = MergeDB(session_pl)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session_pl.expire_all()
    # target replaces source's position in the hierarchy, not a self-loop.
    assert session_pl.get(Place, target_id).parent_id is None
    assert session_pl.get(Place, sibling_id).parent_id == target_id

def test_merge_transfers_associations_and_drops_exact_duplicates(session_pl):
    source = Place(place_name="Source Venue")
    target = Place(place_name="Target Venue")
    session_pl.add_all([source, target])
    session_pl.commit()

    # This association exists on both source and target for the same
    # (entity_type, entity_id, association_type_id) -- an exact duplicate
    # once migrated, and must be dropped rather than duplicated.
    dup_on_target = PlaceAssociation(
        place_id=target.place_id, entity_id=42, entity_type="Track"
    )
    dup_on_source = PlaceAssociation(
        place_id=source.place_id, entity_id=42, entity_type="Track"
    )
    # This one is unique to source and should simply migrate over.
    unique_on_source = PlaceAssociation(
        place_id=source.place_id, entity_id=99, entity_type="Artist"
    )
    session_pl.add_all([dup_on_target, dup_on_source, unique_on_source])
    session_pl.commit()
    target_id = target.place_id

    merger = MergeDB(session_pl)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session_pl.expire_all()
    remaining = (
        session_pl.query(PlaceAssociation).filter_by(place_id=target_id).all()
    )
    keys = sorted((r.entity_type, r.entity_id) for r in remaining)
    assert keys == [("Artist", 99), ("Track", 42)]

# ---- test_merge_role_alias.py ------------------------------------------------
# Role merge now records a RoleAlias for the discarded name, matching
# existing Genre/Artist/Publisher merge-alias behavior (see
# docs/specs/split_and_merge_aliases.md and MergeDB._ALIAS_ON_MERGE_REGISTRY).
@pytest.fixture
def session_ra():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_ra = Session()
    yield session_ra
    session_ra.close()

def test_merging_roles_records_discarded_name_as_alias(session_ra):
    source = Role(role_name="Guitar")
    target = Role(role_name="Guitarist")
    session_ra.add_all([source, target])
    session_ra.commit()
    source_id, target_id = source.role_id, target.role_id

    merger = MergeDB(session_ra)
    result = merger.merge_entities("Role", source_id, target_id)
    assert result is True

    alias = session_ra.query(RoleAlias).filter_by(alias_name="Guitar").one()
    assert alias.role_id == target_id

def test_merging_roles_next_import_resolves_discarded_name_to_target(session_ra):
    source = Role(role_name="Guitar")
    target = Role(role_name="Guitarist")
    session_ra.add_all([source, target])
    session_ra.commit()
    target_id = target.role_id

    merger = MergeDB(session_ra)
    merger.merge_entities("Role", source.role_id, target_id)

    from src.db.db_helpers.get import GetFromDB

    getter = GetFromDB(session_ra)
    resolved = getter.resolve_entity_or_alias("Role", "role_name", "Guitar")
    assert resolved is not None
    assert resolved.role_id == target_id

# ---- test_merge_role_children.py ---------------------------------------------
# Regression tests for Role parent/child reparenting on merge.
#
# `roles.parent_id` is a self-referential FK, so the generic FK-scanning
# merge loop (which skips the entity's own table) never migrates it. Left
# alone, a source role's children fall through to the ORM's default
# delete-time behavior of nulling their parent_id, silently detaching them
# from the hierarchy instead of being reparented onto the target (see bug:
# merging "Musician" into "Performer" did not transfer Musician's children
# to Performer). Mirrors tests/db/test_merge_place.py.
@pytest.fixture
def session_rc():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_rc = Session()
    yield session_rc
    session_rc.close()

def test_merge_reparents_children_onto_target_rc(session_rc):
    source = Role(role_name="Musician")
    target = Role(role_name="Performer")
    session_rc.add_all([source, target])
    session_rc.commit()

    child1 = Role(role_name="Guitarist", parent_id=source.role_id)
    child2 = Role(role_name="Drummer", parent_id=source.role_id)
    session_rc.add_all([child1, child2])
    session_rc.commit()
    child1_id, child2_id, target_id = child1.role_id, child2.role_id, target.role_id

    merger = MergeDB(session_rc)
    result = merger.merge_entities("Role", source.role_id, target.role_id)
    assert result is True

    session_rc.expire_all()
    assert session_rc.get(Role, child1_id).parent_id == target_id
    assert session_rc.get(Role, child2_id).parent_id == target_id
    assert session_rc.get(Role, source.role_id) is None

def test_merge_promotes_branch_when_target_is_descendant_of_source_rc(session_rc):
    """Merging a role into its own grandchild must not create a cycle."""
    source = Role(role_name="Musician")
    session_rc.add(source)
    session_rc.commit()

    mid = Role(role_name="Instrumentalist", parent_id=source.role_id)
    sibling = Role(role_name="Vocalist", parent_id=source.role_id)
    session_rc.add_all([mid, sibling])
    session_rc.commit()

    target = Role(role_name="Guitarist", parent_id=mid.role_id)
    session_rc.add(target)
    session_rc.commit()

    source_id, mid_id, sibling_id, target_id = (
        source.role_id,
        mid.role_id,
        sibling.role_id,
        target.role_id,
    )

    merger = MergeDB(session_rc)
    result = merger.merge_entities("Role", source_id, target_id)
    assert result is True

    session_rc.expire_all()
    assert session_rc.get(Role, mid_id).parent_id is None
    assert session_rc.get(Role, sibling_id).parent_id == target_id
    assert session_rc.get(Role, target_id).parent_id == mid_id
    assert session_rc.get(Role, source_id) is None
