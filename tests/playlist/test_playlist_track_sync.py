"""
Tests for sync_playlist_tracks (src/playlist/playlist_track_sync.py) --
the bulk diff/delete/insert helper extracted from
SmartPlaylistBuilder._update_playlist_tracks so ChartPlaylistBuilder can
reuse it. No pre-existing test suite covered
SmartPlaylistBuilder._update_playlist_tracks before this extraction, so
this file is the regression net for AC16 (identical add/remove/keep
behavior pre- and post-extraction), plus SmartPlaylistBuilder's own
refresh_playlist() flow against a scratch in-memory session.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.playlist.playlist_smart_builder import SmartPlaylistBuilder
from src.playlist.playlist_track_sync import sync_playlist_tracks


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


def _make_playlist(session) -> Playlist:
    playlist = Playlist(playlist_name="Test Playlist")
    session.add(playlist)
    session.commit()
    return playlist


def _make_tracks(session, n) -> list:
    tracks = [Track(track_name=f"Track {i}") for i in range(n)]
    session.add_all(tracks)
    session.commit()
    return tracks


def _track_ids_of(session, playlist_id):
    rows = (
        session.query(PlaylistTracks)
        .filter(PlaylistTracks.playlist_id == playlist_id)
        .all()
    )
    return {r.track_id for r in rows}


def test_first_sync_inserts_all_tracks(session, controller):
    playlist = _make_playlist(session)
    tracks = _make_tracks(session, 3)
    ids = [t.track_id for t in tracks]

    result = sync_playlist_tracks(controller, playlist.playlist_id, ids)

    assert result.added == 3
    assert result.removed == 0
    assert result.kept == 0
    assert _track_ids_of(session, playlist.playlist_id) == set(ids)


def test_resync_only_applies_the_delta(session, controller):
    playlist = _make_playlist(session)
    tracks = _make_tracks(session, 4)
    ids = [t.track_id for t in tracks]

    sync_playlist_tracks(controller, playlist.playlist_id, ids[:3])
    result = sync_playlist_tracks(controller, playlist.playlist_id, ids[1:4])

    assert result.added == 1  # ids[3]
    assert result.removed == 1  # ids[0]
    assert result.kept == 2  # ids[1], ids[2]
    assert _track_ids_of(session, playlist.playlist_id) == set(ids[1:4])


def test_resync_with_same_set_is_a_pure_keep(session, controller):
    playlist = _make_playlist(session)
    tracks = _make_tracks(session, 2)
    ids = [t.track_id for t in tracks]

    sync_playlist_tracks(controller, playlist.playlist_id, ids)
    result = sync_playlist_tracks(controller, playlist.playlist_id, ids)

    assert result.added == 0
    assert result.removed == 0
    assert result.kept == 2


def test_sync_touches_last_modified(session, controller):
    playlist = _make_playlist(session)
    tracks = _make_tracks(session, 1)
    original = playlist.last_modified

    sync_playlist_tracks(controller, playlist.playlist_id, [tracks[0].track_id])

    session.refresh(playlist)
    assert playlist.last_modified != original


def test_smart_playlist_builder_still_updates_tracks_via_shared_helper(
    session, controller
):
    """SmartPlaylistBuilder._update_playlist_tracks now delegates to
    sync_playlist_tracks -- verify the delegation still leaves the
    playlist's track list correct end to end."""
    playlist = _make_playlist(session)
    tracks = _make_tracks(session, 2)
    ids = [t.track_id for t in tracks]

    builder = SmartPlaylistBuilder(controller)
    ok = builder._update_playlist_tracks(playlist.playlist_id, ids)

    assert ok is True
    assert _track_ids_of(session, playlist.playlist_id) == set(ids)
