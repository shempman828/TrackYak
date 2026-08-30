"""Shared fixtures for tests/db/.

Most db_helpers tests run against a throwaway in-memory SQLite database with
foreign-key enforcement turned on (SQLite leaves it off by default). Files that
need a different engine setup can still define their own local ``session``
fixture, which shadows this one.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
