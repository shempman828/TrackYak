"""
Tests for ChartPlaylistWorker (src/charts/chart_playlist_worker.py) against
a scratch in-memory SQLite session -- never music_library.db. Covers signal
wiring (finished/progress/error) and that the worker's DB session gets
released, following tests/charts/test_chart_import_worker.py's pattern of
calling .run() synchronously rather than through a real QThread/event loop.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_playlist_builder import ChartPlaylistStats
from src.charts.chart_playlist_worker import ChartPlaylistWorker
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.track import Track


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def controller(session):
    return StubController(session)


def _seed_one_matched_entry(session):
    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
        last_synced_week=datetime.date(2023, 12, 30),
    )
    track = Track(track_name="Song", recorded_year=1965)
    session.add_all([chart, track])
    session.commit()
    session.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=datetime.date(1965, 6, 1),
            position=1,
            raw_title="Song",
            raw_performer="Someone",
            entity_type="Track",
            entity_id=track.track_id,
            match_score=1.0,
        )
    )
    session.commit()


def test_finished_signal_emits_stats(session, controller):
    _seed_one_matched_entry(session)

    worker = ChartPlaylistWorker(controller)
    results = []
    errors = []
    worker.finished.connect(results.append)
    worker.error.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(results) == 1
    stats = results[0]
    assert isinstance(stats, ChartPlaylistStats)
    assert stats.playlists_created > 0
    assert stats.tracks_added > 0


def test_progress_signal_emits_at_least_once(session, controller):
    _seed_one_matched_entry(session)

    worker = ChartPlaylistWorker(controller)
    progress_calls = []
    worker.progress.connect(lambda done, total: progress_calls.append((done, total)))

    worker.run()

    assert progress_calls
    last_done, last_total = progress_calls[-1]
    assert last_done == last_total


def test_run_releases_db_session_without_error(session, controller, monkeypatch):
    """AC14: the worker must release its pooled DB session on completion.
    Patch Session.remove so we can assert it was actually called, without
    the real music_library.db engine being touched."""
    calls = []
    monkeypatch.setattr(
        "src.db.db_engine.Session.remove", lambda: calls.append(True)
    )
    _seed_one_matched_entry(session)

    ChartPlaylistWorker(controller).run()

    assert calls == [True]
