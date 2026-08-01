"""Regression tests for place merging (MergeDB.merge_entities("Place", ...)).

Place has two quirks the generic FK-scanning merge loop doesn't handle on
its own:

1. `places.parent_id` is a self-referential FK, so the generic loop (which
   skips the entity's own table) never migrates it. Left alone, a source
   place's children would fall through to the ORM's default delete-time
   behavior of nulling their parent_id, silently detaching them from the
   hierarchy instead of being reparented onto the target.
2. `place_associations` has no unique constraint, so the usual
   IntegrityError-triggered "drop duplicate rows" fallback never fires for
   it -- duplicate associations must be dropped explicitly.

Covers MergeDB.merge_entities (src/db/db_helpers/merge.py) against a real
in-memory SQLite session so the ORM cascade path actually executes.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_merge_reparents_children_onto_target(session):
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


def test_merge_promotes_branch_when_target_is_descendant_of_source(session):
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
    session.add_all([dup_on_target, dup_on_source, unique_on_source])
    session.commit()
    target_id = target.place_id

    merger = MergeDB(session)
    result = merger.merge_entities("Place", source.place_id, target.place_id)
    assert result is True

    session.expire_all()
    remaining = (
        session.query(PlaceAssociation).filter_by(place_id=target_id).all()
    )
    keys = sorted((r.entity_type, r.entity_id) for r in remaining)
    assert keys == [("Artist", 99), ("Track", 42)]
