"""Tests for src.db.db_defaults.Defaults.insert_defaults().

Regression focus: Mood is no longer part of the default seed. Real libraries
curate their own mood folksonomy, and the old name-diff seeder re-inserted its
built-in taxonomy on every launch -- so a mood the user deleted kept coming
back. insert_defaults() must not create any Mood rows now, on a fresh DB or a
populated one.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_defaults import Defaults
from src.db.db_tables import Mood, Role
from src.db.db_tables.base import Base


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_insert_defaults_creates_no_moods_on_fresh_db(session_factory):
    Defaults(session_factory).insert_defaults()

    session = session_factory()
    try:
        assert session.query(Mood).count() == 0
        # sanity: the other defaults still seed
        assert session.query(Role).filter_by(role_name="Album Artist").count() == 1
    finally:
        session.close()


def test_insert_defaults_does_not_resurrect_a_deleted_mood(session_factory):
    # User has their own moods; they delete one; relaunch must not re-add it.
    session = session_factory()
    try:
        session.add_all([Mood(mood_name="Happy"), Mood(mood_name="Chill")])
        session.commit()
    finally:
        session.close()

    Defaults(session_factory).insert_defaults()

    session = session_factory()
    try:
        session.query(Mood).filter_by(mood_name="Happy").delete()
        session.commit()
    finally:
        session.close()

    Defaults(session_factory).insert_defaults()

    session = session_factory()
    try:
        names = {name for (name,) in session.query(Mood.mood_name).all()}
        assert names == {"Chill"}
    finally:
        session.close()
