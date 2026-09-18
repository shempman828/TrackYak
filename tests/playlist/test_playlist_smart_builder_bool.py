"""Regression tests for Bool-typed smart playlist criteria casting.

_cast_value had no branch for data_type "Bool" and fell through to a
generic str() cast. Round-tripping a Python bool through SQLite's TEXT
affinity happens to normalize it to "1"/"0" text, which SQLite's own
comparison-affinity rules still match against an integer column -- so the
bug was latent rather than immediately visible through the widget's usual
path. It breaks as soon as the stored text is "True"/"False" instead (e.g.
a differently-behaving value source, or a non-SQLite backend), which
_cast_value must handle correctly regardless of storage quirks.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, SmartPlaylist, SmartPlaylistCriteria
from src.db.db_tables.track import Track
from src.playlist.playlist_smart_builder import SmartPlaylistBuilder


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


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [(True, True), (False, False), ("True", True), ("False", False), ("1", True), ("0", False)],
)
def test_cast_value_handles_bool_representations(controller, stored_value, expected):
    builder = SmartPlaylistBuilder(controller)

    assert builder._cast_value(stored_value, "Bool", "eq") is expected


def test_bool_criterion_matches_correctly_when_stored_as_true_false_text(session, controller):
    """Simulates a value that was stored as literal "True"/"False" text --
    the case _cast_value's old str() fallback got wrong."""
    explicit_track = Track(track_name="Explicit", is_explicit=1)
    clean_track = Track(track_name="Clean", is_explicit=0)
    session.add_all([explicit_track, clean_track])
    session.commit()

    playlist = Playlist(playlist_name="Explicit tracks", is_smart=1)
    session.add(playlist)
    session.commit()
    smart = SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND")
    session.add(smart)
    session.commit()
    # Insert via raw SQL to force the literal text "True", bypassing the
    # String column's usual int-affinity round-trip.
    session.execute(
        text(
            "INSERT INTO smart_playlist_criteria "
            "(smart_playlist_id, field_name, comparison, value, type) "
            "VALUES (:pid, 'is_explicit', 'eq', 'True', 'Bool')"
        ),
        {"pid": playlist.playlist_id},
    )
    session.commit()

    builder = SmartPlaylistBuilder(controller)
    assert builder.refresh_playlist(playlist.playlist_id) is True

    matched_ids = {
        pt.track_id
        for pt in controller.get.get_all_entities(
            "PlaylistTracks", playlist_id=playlist.playlist_id
        )
    }
    assert matched_ids == {explicit_track.track_id}
