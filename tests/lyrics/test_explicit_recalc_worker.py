"""Tests for ExplicitRecalcWorker (src/lyrics/explicit_recalc_worker.py),
the library-wide backfill half of docs/specs/explicit_content_detection.md
(Option 2). Each test maps 1:1 to a numbered acceptance criterion.

Follows tests/charts/test_chart_playlist_worker.py's pattern: a scratch
in-memory SQLite session (never music_library.db) plus a StubController
wrapping the real GetFromDB/UpdateDB helpers, calling .run() synchronously
rather than through a real QThread/event loop.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.foundation import censor
from src.lyrics.explicit_recalc_worker import ExplicitRecalcWorker


@pytest.fixture(autouse=True)
def _isolated_wordlist(tmp_path, monkeypatch):
    wordlist = tmp_path / "explicit_words.txt"
    wordlist.write_text("shit\nfuck\n")
    monkeypatch.setattr(censor, "_WORDLIST_PATH", wordlist)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    yield
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return StubController(session)


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


# AC8 -------------------------------------------------------------------------
def test_backfills_every_null_track_with_lyrics(session, controller):
    explicit_track = _make_track(session, lyrics="this shit is real", is_explicit=None)
    clean_track = _make_track(session, lyrics="a perfectly clean line", is_explicit=None)

    worker = ExplicitRecalcWorker(controller)
    worker.run()

    session.refresh(explicit_track)
    session.refresh(clean_track)
    assert explicit_track.is_explicit
    assert clean_track.is_explicit is not None
    assert not clean_track.is_explicit


def test_finished_signal_reports_scanned_and_flagged_counts(session, controller):
    _make_track(session, lyrics="this shit is real", is_explicit=None)
    _make_track(session, lyrics="a perfectly clean line", is_explicit=None)

    worker = ExplicitRecalcWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, flagged: results.append((scanned, flagged)))
    worker.run()

    assert results == [(2, 1)]


# AC9 -------------------------------------------------------------------------
def test_second_run_is_idempotent_and_reports_zero(session, controller):
    _make_track(session, lyrics="this shit is real", is_explicit=None)

    worker1 = ExplicitRecalcWorker(controller)
    worker1.run()

    worker2 = ExplicitRecalcWorker(controller)
    results = []
    worker2.finished.connect(lambda scanned, flagged: results.append((scanned, flagged)))
    worker2.run()

    assert results == [(0, 0)]


# AC10 ------------------------------------------------------------------------
def test_never_overwrites_a_track_with_existing_is_explicit(session, controller):
    manually_cleared = _make_track(
        session, lyrics="this shit is real", is_explicit=False
    )
    manually_flagged = _make_track(
        session, lyrics="a perfectly clean line", is_explicit=True
    )

    ExplicitRecalcWorker(controller).run()

    session.refresh(manually_cleared)
    session.refresh(manually_flagged)
    assert not manually_cleared.is_explicit
    assert manually_flagged.is_explicit


# AC11 ------------------------------------------------------------------------
def test_cancellation_stops_further_writes_leaving_remaining_tracks_null(
    session, controller
):
    t1 = _make_track(session, lyrics="this shit is real", is_explicit=None)
    t2 = _make_track(session, lyrics="another shit line", is_explicit=None)

    worker = ExplicitRecalcWorker(controller)
    worker.request_cancel()
    worker.run()

    session.refresh(t1)
    session.refresh(t2)
    assert t1.is_explicit is None
    assert t2.is_explicit is None


# AC13 (batch path) ------------------------------------------------------------
def test_skips_tracks_with_null_or_empty_lyrics(session, controller):
    null_lyrics = _make_track(session, lyrics=None, is_explicit=None)
    empty_lyrics = _make_track(session, lyrics="   ", is_explicit=None)

    worker = ExplicitRecalcWorker(controller)
    results = []
    worker.finished.connect(lambda scanned, flagged: results.append((scanned, flagged)))
    worker.run()

    assert results == [(0, 0)]
    session.refresh(null_lyrics)
    session.refresh(empty_lyrics)
    assert null_lyrics.is_explicit is None
    assert empty_lyrics.is_explicit is None


def test_run_releases_db_session_without_error(session, controller, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.db.db_engine.Session.remove", lambda: calls.append(True)
    )
    _make_track(session, lyrics="this shit is real", is_explicit=None)

    ExplicitRecalcWorker(controller).run()

    assert calls == [True]
