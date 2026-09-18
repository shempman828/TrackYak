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

    session.add(
        PlaylistTracks(
            playlist_id=playlist.playlist_id, track_id=already_in_playlist.track_id, position=1
        )
    )
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
