"""Regression test: handle_drop used to run one get_all_entities query per
dropped track just to check for duplicates (N+1), instead of reusing the
PlaylistTracks rows it had already fetched for the position calculation.
The fix must still behave the same: skip tracks already in the playlist,
and not double-add a track ID repeated within the same drop payload.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.playlist.playlist_tracks_window import PlaylistTracksWindow


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
