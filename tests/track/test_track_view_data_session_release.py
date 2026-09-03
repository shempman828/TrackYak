"""Regression: TrackLookupCacheWorker must release its background thread's
scoped_session in run() (finally), or every Tracks-nav revisit leaks the
pooled DB connection that thread checked out (plus its open WAL read
transaction and ~2 MB SQLite page cache). Over a long view-switching
session this was the bulk of a multi-GB RSS climb.

See src/track/track_view_data.py TrackLookupCacheWorker.run().
"""

import threading

from PySide6.QtCore import QThread
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, Artist, Disc, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base
from src.track.track_view_data import TrackLookupCacheWorker


class _Controller:
    def __init__(self, session_factory):
        self.get = GetFromDB(session_factory)
        self.SessionFactory = session_factory


@pytest.fixture
def scoped_factory(tmp_path, monkeypatch):
    """A scoped_session over a real file DB (QueuePool, like production), wired
    in as src.db.db_engine.Session so the worker's `from src.db.db_engine
    import Session` in its finally block resolves to it."""
    engine = create_engine(f"sqlite:///{tmp_path / 'lib.db'}")
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))

    seed = factory()
    role = Role(role_id=1, role_name="Primary Artist")
    album = Album(album_id=1, album_name="A")
    disc = Disc(disc_id=1, album_id=1, disc_number=1)
    track = Track(track_id=1, track_name="t", album_id=1, disc_id=1, needs_tag_write=False)
    artist = Artist(artist_id=1, artist_name="Somebody")
    seed.add_all([role, album, disc, track, artist])
    seed.add(TrackArtistRole(track_id=1, artist_id=1, role_id=1))
    seed.commit()
    factory.remove()

    monkeypatch.setattr("src.db.db_engine.Session", factory, raising=False)
    yield factory
    factory.remove()
    engine.dispose()


def _run_on_thread(qapp, worker):
    thread = QThread()
    worker.moveToThread(thread)
    done = {}
    thread.started.connect(worker.run)
    worker.finished.connect(lambda *a: done.setdefault("ok", True))
    worker.error.connect(lambda msg: done.setdefault("err", msg))
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    thread.start()

    deadline = 5000
    while not thread.isFinished() and deadline > 0:
        qapp.processEvents()
        thread.wait(50)
        deadline -= 50
    assert thread.isFinished(), "worker thread did not finish"
    thread.deleteLater()
    return done


def test_worker_releases_thread_local_session(qapp, scoped_factory, monkeypatch):
    real_remove = scoped_factory.remove
    calls = {}

    def spy_remove():
        calls["thread"] = threading.get_ident()
        calls["n"] = calls.get("n", 0) + 1
        return real_remove()

    monkeypatch.setattr(scoped_factory, "remove", spy_remove)

    worker = TrackLookupCacheWorker(_Controller(scoped_factory))
    done = _run_on_thread(qapp, worker)

    assert done.get("ok") and "err" not in done
    # run() must have released its scoped_session...
    assert calls.get("n", 0) >= 1, (
        "run() never called Session.remove() — connection leaks per revisit"
    )
    # ...on the worker's own thread (scoped_session keys by thread identity,
    # so releasing from any other thread would free the wrong session).
    assert calls["thread"] != threading.get_ident()
