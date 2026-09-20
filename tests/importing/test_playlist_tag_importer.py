"""Tests for PlaylistTagImporter, which reconstructs playlist membership from
PLAYLIST metadata tags during import (extracted out of TrackImporter)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.importing.playlist_tag_importer import PlaylistTagImporter


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


def _track(session, name="Some Track"):
    track = Track(track_name=name, track_file_path=f"/music/{name}.flac")
    session.add(track)
    session.commit()
    return track


def test_vorbis_playlist_tag_creates_playlist_and_membership(controller):
    session = controller.get.session
    track = _track(session)

    PlaylistTagImporter(controller)._process_playlist_tags(track, {"PLAYLIST": "Road Trip"})
    session.commit()

    playlist = session.query(Playlist).filter_by(playlist_name="Road Trip").first()
    assert playlist is not None
    membership = (
        session.query(PlaylistTracks)
        .filter_by(playlist_id=playlist.playlist_id, track_id=track.track_id)
        .first()
    )
    assert membership is not None
    assert membership.position == 1


def test_id3_playlist_tag_supports_multiple_names(controller):
    session = controller.get.session
    track = _track(session)

    PlaylistTagImporter(controller)._process_playlist_tags(
        track, {"TXXX:PLAYLIST": "Road Trip ; Workout"}
    )
    session.commit()

    names = {p.playlist_name for p in session.query(Playlist).all()}
    assert names == {"Road Trip", "Workout"}


def test_track_already_in_playlist_is_not_duplicated(controller):
    session = controller.get.session
    track = _track(session)

    PlaylistTagImporter(controller)._process_playlist_tags(track, {"PLAYLIST": "Road Trip"})
    session.commit()
    PlaylistTagImporter(controller)._process_playlist_tags(track, {"PLAYLIST": "Road Trip"})
    session.commit()

    playlist = session.query(Playlist).filter_by(playlist_name="Road Trip").first()
    rows = (
        session.query(PlaylistTracks)
        .filter_by(playlist_id=playlist.playlist_id, track_id=track.track_id)
        .all()
    )
    assert len(rows) == 1


def test_no_playlist_tags_creates_nothing(controller):
    session = controller.get.session
    track = _track(session)

    PlaylistTagImporter(controller)._process_playlist_tags(track, {})
    session.commit()

    assert session.query(Playlist).count() == 0
