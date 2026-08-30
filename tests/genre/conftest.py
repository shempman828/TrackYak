"""Shared fixtures for tests/genre/.

GenreView.load_genres() runs GenreLoaderWorker on a real background QThread
(see docs/specs/genre_sort_by_count.md) so opening the genre tab never
blocks the UI. For deterministic tests, every test in this package runs the
worker's run() directly instead of starting a real OS thread -- the same
technique tests/charts/test_chart_import_worker.py uses for CancellableWorker
subclasses -- applied here via a class-level patch so `GenreView(controller)`
can be used exactly as it was before this feature, with no event-loop
plumbing needed in each test.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.genre.genre_view import GenreLoaderWorker


@pytest.fixture(autouse=True)
def _run_genre_loader_synchronously(monkeypatch):
    monkeypatch.setattr(GenreLoaderWorker, "start", GenreLoaderWorker.run)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
