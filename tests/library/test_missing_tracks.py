"""Regression tests: Find Missing Tracks scans in a worker thread, not in MissingTracks.__init__ on the UI thread."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.library import missing_tracks
from src.library.missing_tracks import MissingTracks, MissingTracksWorker


@pytest.fixture
def session():
    # expire_on_commit=False to match the app's real Session -- get.py commits after every query.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def library(session, tmp_path):
    present = tmp_path / "present.mp3"
    present.write_bytes(b"")
    tracks = {
        "present": Track(track_name="Present", track_file_path=str(present)),
        "gone": Track(track_name="Gone", track_file_path=str(tmp_path / "gone.mp3")),
        "no_path": Track(track_name="No Path", track_file_path=None),
    }
    session.add_all(tracks.values())
    session.commit()
    return SimpleNamespace(controller=SimpleNamespace(get=GetFromDB(session)), tracks=tracks)


def test_init_and_start_do_not_scan_on_the_calling_thread(qapp, monkeypatch, library):
    monkeypatch.setattr(MissingTracksWorker, "start", lambda self: None)
    monkeypatch.setattr(library.controller.get, "get_all_entities", lambda *a, **k: pytest.fail("scanned on the UI thread"))

    scan = MissingTracks(library.controller)
    scan.start()

    assert scan.is_running


def test_worker_reports_missing_track_ids(qapp, monkeypatch, library):
    monkeypatch.setattr(MissingTracksWorker, "_release_db_session", staticmethod(lambda: None))
    worker = MissingTracksWorker(library.controller)
    results = []
    worker.results_ready.connect(lambda ids, cancelled: results.append((sorted(ids), cancelled)))

    worker.run()

    expected = sorted([library.tracks["gone"].track_id, library.tracks["no_path"].track_id])
    assert results == [(expected, False)]


def test_results_open_dialog_with_the_missing_tracks(qapp, monkeypatch, library):
    shown = []
    monkeypatch.setattr(MissingTracks, "show_missing_tracks", lambda self, missing: shown.append(missing))
    scan = MissingTracks(library.controller)

    scan._on_results([library.tracks["gone"].track_id], False)

    assert [t.track_name for t in shown[0]] == ["Gone"]


def test_cancelled_results_do_not_open_dialog(qapp, monkeypatch, library):
    monkeypatch.setattr(missing_tracks, "BaseTrackView", lambda **k: pytest.fail("dialog opened after cancel"))
    scan = MissingTracks(library.controller)

    scan._on_results([library.tracks["gone"].track_id], True)
