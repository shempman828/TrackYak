"""Tests for src.db.db_defaults.Defaults.insert_defaults().

Moods and tags are user-curated, so insert_defaults() must never seed them --
the old name-diff seeder re-inserted its built-in moods on every launch, so a
mood the user deleted kept coming back.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_defaults import Defaults
from src.db.db_tables import Mood, Role, Tag, TagType
from src.db.db_tables.base import Base


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _count(session_factory, query):
    session = session_factory()
    try:
        return query(session)
    finally:
        session.close()


def test_fresh_db_seeds_roles_but_no_moods_or_tags(session_factory):
    Defaults(session_factory).insert_defaults()

    assert _count(session_factory, lambda s: s.query(Role).filter_by(role_name="Album Artist").count()) == 1
    assert _count(session_factory, lambda s: s.query(Mood).count()) == 0
    assert _count(session_factory, lambda s: s.query(TagType).count() + s.query(Tag).count()) == 0


def test_insert_defaults_does_not_resurrect_a_deleted_mood(session_factory):
    session = session_factory()
    session.add_all([Mood(mood_name="Happy"), Mood(mood_name="Chill")])
    session.commit()
    Defaults(session_factory).insert_defaults()
    session.query(Mood).filter_by(mood_name="Happy").delete()
    session.commit()
    session.close()

    Defaults(session_factory).insert_defaults()

    assert _count(session_factory, lambda s: {n for (n,) in s.query(Mood.mood_name).all()}) == {"Chill"}
