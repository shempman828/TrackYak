"""Regression test for DuplicateScanWorker's cross-thread lazy-load bug.

_score_pair_metadata (via _get_primary_artist_string/_get_album_string) reads
track.artist_roles/.album from DuplicateScanWorker's own QThread. If those
relationships aren't eager-loaded before the tracks cross into the worker
thread, accessing them lazily there checks out a second, never-released
pooled DB connection on that thread -- the same QueuePool-exhaustion bug
class documented in src/common/cancellable_worker.py's docstring.
DuplicateFinderDialog._start_scan() now passes load_options to eager-load
everything that path touches; this test locks that in at the actual call site.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
from src.library.duplicate_finder import DuplicateFinderDialog, DuplicateScanWorker


@pytest.fixture
def session():
    # expire_on_commit=False to match the app's real Session (src/db/db_engine.py) --
    # get.py's read-path commit()s after every query, which would otherwise expire
    # (and require a session to reload) the very relationships this test checks were
    # already eager-loaded.
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def test_start_scan_eager_loads_what_the_worker_thread_needs(qapp, monkeypatch, session):
    artist = Artist(artist_name="Test Artist")
    role = Role(role_name="Primary Artist")
    track = Track(track_name="Song")
    session.add_all([artist, role, track])
    session.commit()
    session.add(TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=role.role_id))
    session.commit()

    # Don't actually spin up the worker thread -- just check what it was handed.
    monkeypatch.setattr(DuplicateScanWorker, "start", lambda self: None)

    controller = SimpleNamespace(get=GetFromDB(session))
    dialog = DuplicateFinderDialog(controller)
    try:
        dialog._start_scan()
        (fetched,) = dialog._worker._tracks

        session.close()  # simulate crossing to the worker thread: no session left to lazy-load with

        # Must not raise DetachedInstanceError: already populated by _start_scan's
        # load_options, so DuplicateScanWorker.run() never needs to lazy-load these.
        assert len(fetched.artist_roles) == 1
        assert fetched.artist_roles[0].artist.artist_name == "Test Artist"
        assert fetched.artist_roles[0].role.role_name == "Primary Artist"
        assert fetched.album is None
    finally:
        dialog.deleteLater()
