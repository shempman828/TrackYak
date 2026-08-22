"""Regression tests for Role parent/child reparenting on merge.

`roles.parent_id` is a self-referential FK, so the generic FK-scanning
merge loop (which skips the entity's own table) never migrates it. Left
alone, a source role's children fall through to the ORM's default
delete-time behavior of nulling their parent_id, silently detaching them
from the hierarchy instead of being reparented onto the target (see bug:
merging "Musician" into "Performer" did not transfer Musician's children
to Performer). Mirrors tests/db/test_merge_place.py.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role


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


def test_merge_promotes_branch_when_target_is_descendant_of_source(session):
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
