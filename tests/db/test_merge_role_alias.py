"""Role merge now records a RoleAlias for the discarded name, matching
existing Genre/Artist/Publisher merge-alias behavior (see
docs/specs/split_and_merge_aliases.md and MergeDB._ALIAS_ON_MERGE_REGISTRY).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.merge import MergeDB
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role, RoleAlias


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
