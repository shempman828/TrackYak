"""Regression test for bug #252: merging artists could silently drop the
losing artist's album_role_association rows instead of re-pointing them to
the surviving artist.

MergeDB.merge_entities() migrates FK rows with a raw SQL UPDATE, then does
`session.delete(source_entity)` so ORM cascade rules apply to any tables it
didn't explicitly touch. But Artist.album_roles is
`cascade="all, delete-orphan"` with `passive_deletes=True`, which only skips
*loading* the collection on delete -- it does not stop cascade-delete from
acting on a collection that was already loaded earlier in the session (e.g.
by viewing the artist's detail panel, which reads `artist.albums` ->
`artist.album_roles`). In that case cascade-delete walked the stale
in-memory list and deleted the just-migrated rows by primary key instead of
leaving them re-pointed to the target artist.

Covers MergeDB.merge_entities (src/db/db_helpers/merge.py) against a real
in-memory SQLite session so the ORM cascade path actually executes.
"""

import pytest

from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import AlbumRoleAssociation
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.role import Role, RoleAlias


# ---- test_merge__self_base.py ------------------------------------------------
def _make_artists_album_and_role(session):
    source = Artist(artist_name="Source Artist")
    target = Artist(artist_name="Target Artist")
    album = Album(album_name="Some Album")
    role = Role(role_name="Album Artist")
    session.add_all([source, target, album, role])
    session.commit()

    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=source.artist_id, role_id=role.role_id
        )
    )
    session.commit()
    return source, target, album, role


def test_merge_preserves_album_role_when_collection_preloaded(session):
    source, target, album, _role = _make_artists_album_and_role(session)

    # Simulate viewing the source artist's detail panel before merging,
    # which loads `artist.album_roles` into the session's identity map.
    assert len(source.album_roles) == 1

    merger = MergeDB(session)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = session.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
    assert len(remaining) == 1
    assert remaining[0].artist_id == target.artist_id


def test_merge_preserves_album_role_when_collection_not_preloaded(session):
    source, target, album, _role = _make_artists_album_and_role(session)

    merger = MergeDB(session)
    result = merger.merge_entities("Artist", source.artist_id, target.artist_id)

    assert result is True

    remaining = session.query(AlbumRoleAssociation).filter_by(album_id=album.album_id).all()
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
# in-memory SQLite session so the ORM cascade path actually executes.
def test_merge_reparents_children_onto_target_pl(session):
    source = Place(place_name="Source Region")
    target = Place(place_name="Target Region")
    session.add_all([source, target])
    session.commit()

    child1 = Place(place_name="Child One", parent_id=source.place_id)
    child2 = Place(place_name="Child Two", parent_id=source.place_id)
    session.add_all([child1, child2])
    session.commit()
    child1_id, child2_id, target_id = child1.place_id, child2.place_id, target.place_id

    merger = MergeDB(session)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session.expire_all()
    assert session.get(Place, child1_id).parent_id == target_id
    assert session.get(Place, child2_id).parent_id == target_id
    assert session.get(Place, source.place_id) is None


def test_merge_promotes_branch_when_target_is_descendant_of_source_pl(session):
    """Merging a place into its own grandchild must not create a cycle."""
    source = Place(place_name="Country")
    session.add(source)
    session.commit()

    mid = Place(place_name="State", parent_id=source.place_id)
    sibling = Place(place_name="Other State", parent_id=source.place_id)
    session.add_all([mid, sibling])
    session.commit()

    target = Place(place_name="City", parent_id=mid.place_id)
    session.add(target)
    session.commit()

    source_id, mid_id, sibling_id, target_id = (
        source.place_id,
        mid.place_id,
        sibling.place_id,
        target.place_id,
    )

    merger = MergeDB(session)
    result = merger.merge_entities("Place", source_id, target_id)
    assert result is True

    session.expire_all()
    # The branch leading to target (mid) is promoted to source's old
    # position instead of being pointed at target, which would have been
    # a two-node cycle (mid -> target -> mid).
    assert session.get(Place, mid_id).parent_id is None
    # Unrelated children of source reparent onto target normally.
    assert session.get(Place, sibling_id).parent_id == target_id
    # Target itself is untouched -- still a child of mid, no self-loop.
    assert session.get(Place, target_id).parent_id == mid_id
    assert session.get(Place, source_id) is None


def test_merge_promotes_target_when_target_is_direct_child_of_source(session):
    source = Place(place_name="Parent Place")
    session.add(source)
    session.commit()

    target = Place(place_name="Child Place", parent_id=source.place_id)
    sibling = Place(place_name="Sibling Place", parent_id=source.place_id)
    session.add_all([target, sibling])
    session.commit()
    target_id, sibling_id = target.place_id, sibling.place_id

    merger = MergeDB(session)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session.expire_all()
    # target replaces source's position in the hierarchy, not a self-loop.
    assert session.get(Place, target_id).parent_id is None
    assert session.get(Place, sibling_id).parent_id == target_id


def test_merge_transfers_associations_and_drops_exact_duplicates(session):
    source = Place(place_name="Source Venue")
    target = Place(place_name="Target Venue")
    session.add_all([source, target])
    session.commit()

    # This association exists on both source and target for the same
    # (entity_type, entity_id, association_type_id) -- an exact duplicate
    # once migrated, and must be dropped rather than duplicated.
    dup_on_target = PlaceAssociation(place_id=target.place_id, entity_id=42, entity_type="Track")
    dup_on_source = PlaceAssociation(place_id=source.place_id, entity_id=42, entity_type="Track")
    # This one is unique to source and should simply migrate over.
    unique_on_source = PlaceAssociation(
        place_id=source.place_id, entity_id=99, entity_type="Artist"
    )
    session.add_all([dup_on_target, dup_on_source, unique_on_source])
    session.commit()
    target_id = target.place_id

    merger = MergeDB(session)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session.expire_all()
    remaining = session.query(PlaceAssociation).filter_by(place_id=target_id).all()
    keys = sorted((r.entity_type, r.entity_id) for r in remaining)
    assert keys == [("Artist", 99), ("Track", 42)]


# ---- test_merge_role_alias.py ------------------------------------------------
# Role merge now records a RoleAlias for the discarded name, matching
# existing Genre/Artist/Publisher merge-alias behavior (see
# docs/specs/split_and_merge_aliases.md and MergeDB._ALIAS_ON_MERGE_REGISTRY).
def test_merging_roles_records_discarded_name_as_alias(session):
    source = Role(role_name="Guitar")
    target = Role(role_name="Guitarist")
    session.add_all([source, target])
    session.commit()
    source_id, target_id = source.role_id, target.role_id

    merger = MergeDB(session)
    result = merger.merge_entities("Role", source_id, target_id)
    assert result is True

    alias = session.query(RoleAlias).filter_by(alias_name="Guitar").one()
    assert alias.role_id == target_id


def test_merging_roles_next_import_resolves_discarded_name_to_target(session):
    source = Role(role_name="Guitar")
    target = Role(role_name="Guitarist")
    session.add_all([source, target])
    session.commit()
    target_id = target.role_id

    merger = MergeDB(session)
    merger.merge_entities("Role", source.role_id, target_id)

    from src.db.db_helpers.get import GetFromDB

    getter = GetFromDB(session)
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
def test_merge_reparents_children_onto_target_rc(session):
    source = Role(role_name="Musician")
    target = Role(role_name="Performer")
    session.add_all([source, target])
    session.commit()

    child1 = Role(role_name="Guitarist", parent_id=source.role_id)
    child2 = Role(role_name="Drummer", parent_id=source.role_id)
    session.add_all([child1, child2])
    session.commit()
    child1_id, child2_id, target_id = child1.role_id, child2.role_id, target.role_id

    merger = MergeDB(session)
    result = merger.merge_entities("Role", source.role_id, target.role_id)
    assert result is True

    session.expire_all()
    assert session.get(Role, child1_id).parent_id == target_id
    assert session.get(Role, child2_id).parent_id == target_id
    assert session.get(Role, source.role_id) is None


def test_merge_promotes_branch_when_target_is_descendant_of_source_rc(session):
    """Merging a role into its own grandchild must not create a cycle."""
    source = Role(role_name="Musician")
    session.add(source)
    session.commit()

    mid = Role(role_name="Instrumentalist", parent_id=source.role_id)
    sibling = Role(role_name="Vocalist", parent_id=source.role_id)
    session.add_all([mid, sibling])
    session.commit()

    target = Role(role_name="Guitarist", parent_id=mid.role_id)
    session.add(target)
    session.commit()

    source_id, mid_id, sibling_id, target_id = (
        source.role_id,
        mid.role_id,
        sibling.role_id,
        target.role_id,
    )

    merger = MergeDB(session)
    result = merger.merge_entities("Role", source_id, target_id)
    assert result is True

    session.expire_all()
    assert session.get(Role, mid_id).parent_id is None
    assert session.get(Role, sibling_id).parent_id == target_id
    assert session.get(Role, target_id).parent_id == mid_id
    assert session.get(Role, source_id) is None


# ---- managed picture reconciliation after merge ---------------------------
# MergeDB.merge_entities must clean up the artist_images/ (or publisher_logos/)
# file for the merged-away entity: unlink whichever picture the merge did not
# keep, and rename a kept source picture onto the surviving entity's id.

from src.core import asset_paths  # noqa: E402
from src.db.db_tables.publisher import Publisher  # noqa: E402


@pytest.fixture
def managed_dirs(tmp_path, monkeypatch):
    artist_dir = tmp_path / "artist_images"
    publisher_dir = tmp_path / "publisher_logos"
    artist_dir.mkdir()
    publisher_dir.mkdir()
    monkeypatch.setattr(asset_paths, "ARTIST_IMAGES_DIR", artist_dir)
    monkeypatch.setattr(asset_paths, "PUBLISHER_LOGOS_DIR", publisher_dir)
    return artist_dir, publisher_dir


def test_merge_keeps_source_picture_renames_it_onto_target(session, managed_dirs):
    artist_dir, _ = managed_dirs
    # Distinct extensions so the discarded loser file and the renamed
    # survivor file are different paths.
    src_pic = artist_dir / "1_Source.jpg"
    src_pic.write_bytes(b"src")
    tgt_pic = artist_dir / "2_Target.png"
    tgt_pic.write_bytes(b"tgt")

    source = Artist(artist_name="Source", profile_pic_path=str(src_pic))
    target = Artist(artist_name="Target", profile_pic_path=str(tgt_pic))
    session.add_all([source, target])
    session.commit()
    source_id, target_id = source.artist_id, target.artist_id

    MergeDB(session).merge_entities(
        "Artist", source_id, target_id, {"profile_pic_path": str(src_pic)}
    )

    session.expire_all()
    survivor = session.get(Artist, target_id)
    expected = artist_dir / f"{target_id}_Target.jpg"
    assert survivor.profile_pic_path == str(expected)
    assert expected.read_bytes() == b"src"
    assert not src_pic.exists()  # renamed away
    assert not tgt_pic.exists()  # discarded loser, unlinked


def test_merge_keeps_target_picture_unlinks_source_file(session, managed_dirs):
    artist_dir, _ = managed_dirs
    src_pic = artist_dir / "1_Source.jpg"
    src_pic.write_bytes(b"src")
    tgt_pic = artist_dir / "2_Target.jpg"
    tgt_pic.write_bytes(b"tgt")

    source = Artist(artist_name="Source", profile_pic_path=str(src_pic))
    target = Artist(artist_name="Target", profile_pic_path=str(tgt_pic))
    session.add_all([source, target])
    session.commit()
    source_id, target_id = source.artist_id, target.artist_id

    MergeDB(session).merge_entities(
        "Artist", source_id, target_id, {"profile_pic_path": str(tgt_pic)}
    )

    session.expire_all()
    assert session.get(Artist, target_id).profile_pic_path == str(tgt_pic)
    assert tgt_pic.exists()
    assert not src_pic.exists()


def test_merge_publisher_logo_reconciled(session, managed_dirs):
    _, publisher_dir = managed_dirs
    src_logo = publisher_dir / "1_Src.png"
    src_logo.write_bytes(b"s")

    source = Publisher(publisher_name="Src", logo_path=str(src_logo))
    target = Publisher(publisher_name="Dst")
    session.add_all([source, target])
    session.commit()
    source_id, target_id = source.publisher_id, target.publisher_id

    MergeDB(session).merge_entities("Publisher", source_id, target_id, {"logo_path": str(src_logo)})

    session.expire_all()
    expected = publisher_dir / f"{target_id}_Dst.png"
    assert session.get(Publisher, target_id).logo_path == str(expected)
    assert expected.exists()
    assert not src_logo.exists()
