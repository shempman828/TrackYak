"""Playlist track membership: batched drops onto the tracks window and
track sync.
"""

import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.playlist.playlist_track_sync import sync_playlist_tracks
from src.playlist.playlist_tracks_window import PlaylistTracksWindow
from src.playlist.smart.playlist_smart_builder import SmartPlaylistBuilder

# ---------------------------------------------------------------------------
# Regression test: handle_drop used to run one get_all_entities query per
# ---------------------------------------------------------------------------


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


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


class _FakeMimeData:
    def __init__(self, text):
        self._text = text

    def hasFormat(self, fmt):
        return fmt == "application/x-track-id"

    def data(self, fmt):
        return SimpleNamespace(data=lambda: self._text.encode())


class _FakeDropEvent:
    def __init__(self, text):
        self._mime = _FakeMimeData(text)
        self.accepted = None

    def mimeData(self):
        return self._mime

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_drop_skips_existing_and_ignores_intra_drop_duplicates(qapp, session, controller):
    playlist = Playlist(playlist_name="Test")
    session.add(playlist)
    session.commit()
    tracks = [Track(track_name=f"T{i}") for i in range(3)]
    session.add_all(tracks)
    session.commit()
    already_in_playlist, new_track, also_new = tracks

    session.add(PlaylistTracks(playlist_id=playlist.playlist_id, track_id=already_in_playlist.track_id, position=1))
    session.commit()

    window = PlaylistTracksWindow(playlist.playlist_id, controller)
    ids = [already_in_playlist.track_id, new_track.track_id, new_track.track_id, also_new.track_id]
    event = _FakeDropEvent(",".join(str(i) for i in ids))

    window.handle_drop(event)

    rows = session.query(PlaylistTracks).filter_by(playlist_id=playlist.playlist_id).all()
    track_ids = {r.track_id for r in rows}
    assert track_ids == {already_in_playlist.track_id, new_track.track_id, also_new.track_id}
    # The repeated new_track.track_id in the payload must only be inserted once.
    assert len(rows) == 3
    assert event.accepted is True


def test_drop_emits_tracks_changed(qapp, session, controller, monkeypatch):
    monkeypatch.setattr("src.playlist.playlist_tracks_window.show_status_message", lambda *a: None)
    playlist = Playlist(playlist_name="Test")
    track = Track(track_name="T")
    session.add_all([playlist, track])
    session.commit()
    window = PlaylistTracksWindow(playlist.playlist_id, controller)
    changed = []
    window.tracks_changed.connect(changed.append)

    window.handle_drop(_FakeDropEvent(str(track.track_id)))

    assert changed == [playlist.playlist_id]


def test_smart_playlist_window_has_no_remove_action(qapp, session, controller):
    playlist = Playlist(playlist_name="Smart", is_smart=1)
    session.add(playlist)
    session.commit()

    window = PlaylistTracksWindow(playlist.playlist_id, controller)

    assert not hasattr(window, "remove_from_playlist_action")


def test_close_saves_geometry_and_deletes_the_window(qapp, session, controller):
    from PySide6.QtCore import Qt

    saved = {}
    controller.settings = SimpleNamespace(value=lambda key: None, setValue=lambda key, value: saved.__setitem__(key, value))
    playlist = Playlist(playlist_name="Test")
    session.add(playlist)
    session.commit()
    window = PlaylistTracksWindow(playlist.playlist_id, controller)
    # Qt clears the attribute itself when close() schedules the delete.
    assert window.testAttribute(Qt.WA_DeleteOnClose)

    window.close()

    assert f"playlist_window_{playlist.playlist_id}_geometry" in saved


# ---------------------------------------------------------------------------
# Tests for sync_playlist_tracks (src/playlist/playlist_track_sync.py) --
# ---------------------------------------------------------------------------


class StubControllerSync:
    def __init__(self, session_sync):
        self.get = GetFromDB(session_sync)
        self.add = AddToDB(session_sync)
        self.update = UpdateDB(session_sync)


@pytest.fixture
def session_sync():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_sync = Session()
    yield session_sync
    session_sync.close()


@pytest.fixture
def controller_sync(session_sync):
    return StubControllerSync(session_sync)


def _make_playlist(session_sync) -> Playlist:
    playlist = Playlist(playlist_name="Test Playlist")
    session_sync.add(playlist)
    session_sync.commit()
    return playlist


def _make_tracks(session_sync, n) -> list:
    tracks = [Track(track_name=f"Track {i}") for i in range(n)]
    session_sync.add_all(tracks)
    session_sync.commit()
    return tracks


def _track_ids_of(session_sync, playlist_id):
    rows = (
        session_sync.query(PlaylistTracks)
        .filter(PlaylistTracks.playlist_id == playlist_id)
        .all()
    )
    return {r.track_id for r in rows}


def test_first_sync_inserts_all_tracks(session_sync, controller_sync):
    playlist = _make_playlist(session_sync)
    tracks = _make_tracks(session_sync, 3)
    ids = [t.track_id for t in tracks]

    result = sync_playlist_tracks(controller_sync, playlist.playlist_id, ids)

    assert result.added == 3
    assert result.removed == 0
    assert result.kept == 0
    assert _track_ids_of(session_sync, playlist.playlist_id) == set(ids)


def test_resync_only_applies_the_delta(session_sync, controller_sync):
    playlist = _make_playlist(session_sync)
    tracks = _make_tracks(session_sync, 4)
    ids = [t.track_id for t in tracks]

    sync_playlist_tracks(controller_sync, playlist.playlist_id, ids[:3])
    result = sync_playlist_tracks(controller_sync, playlist.playlist_id, ids[1:4])

    assert result.added == 1  # ids[3]
    assert result.removed == 1  # ids[0]
    assert result.kept == 2  # ids[1], ids[2]
    assert _track_ids_of(session_sync, playlist.playlist_id) == set(ids[1:4])


def test_resync_with_same_set_is_a_pure_keep(session_sync, controller_sync):
    playlist = _make_playlist(session_sync)
    tracks = _make_tracks(session_sync, 2)
    ids = [t.track_id for t in tracks]

    sync_playlist_tracks(controller_sync, playlist.playlist_id, ids)
    result = sync_playlist_tracks(controller_sync, playlist.playlist_id, ids)

    assert result.added == 0
    assert result.removed == 0
    assert result.kept == 2


def test_sync_touches_last_modified(session_sync, controller_sync):
    playlist = _make_playlist(session_sync)
    tracks = _make_tracks(session_sync, 1)
    original = playlist.last_modified

    sync_playlist_tracks(controller_sync, playlist.playlist_id, [tracks[0].track_id])

    session_sync.refresh(playlist)
    assert playlist.last_modified != original


def test_smart_playlist_builder_still_updates_tracks_via_shared_helper(
    session_sync, controller_sync
):
    """SmartPlaylistBuilder._update_playlist_tracks now delegates to
    sync_playlist_tracks -- verify the delegation still leaves the
    playlist's track list correct end to end."""
    playlist = _make_playlist(session_sync)
    tracks = _make_tracks(session_sync, 2)
    ids = [t.track_id for t in tracks]

    builder = SmartPlaylistBuilder(controller_sync)
    ok = builder._update_playlist_tracks(playlist.playlist_id, ids)

    assert ok is True
    assert _track_ids_of(session_sync, playlist.playlist_id) == set(ids)
