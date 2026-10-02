"""Tests for MoodTracksWindow.load_tracks (src/mood/mood_tracks.py): direct
and recursive (sub-mood) track loading must return the right deduped tracks
with a fixed number of queries -- not one query per child mood or per track.
"""

from PySide6.QtWidgets import QWidget
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood import mood_tracks
from src.mood.mood_tracks import MoodTracksWindow


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


class _StubTrackView(QWidget):
    def __init__(self, controller, tracks, title):
        super().__init__()
        self.loaded = None

    def load_data(self, tracks):
        self.loaded = tracks


@pytest.fixture
def setup(qapp, monkeypatch):
    monkeypatch.setattr(mood_tracks, "BaseTrackView", _StubTrackView)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Match the app session: query_entities commits after each read.
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    happy = Mood(mood_name="Happy")
    session.add(happy)
    session.flush()
    joyful = Mood(mood_name="Joyful", parent_id=happy.mood_id)
    session.add(joyful)
    session.flush()
    ecstatic = Mood(mood_name="Ecstatic", parent_id=joyful.mood_id)
    sad = Mood(mood_name="Sad")
    session.add_all([ecstatic, sad])
    session.flush()

    tracks = {name: Track(track_name=name) for name in ("own", "child", "grandchild", "shared", "unrelated")}
    session.add_all(tracks.values())
    session.flush()
    for mood, name in [(happy, "own"), (joyful, "child"), (ecstatic, "grandchild"), (happy, "shared"), (ecstatic, "shared"), (sad, "unrelated")]:
        session.add(MoodTrackAssociation(mood_id=mood.mood_id, track_id=tracks[name].track_id))
    session.commit()

    selects = []
    event.listen(engine, "before_cursor_execute", lambda _conn, _cur, stmt, *_a: selects.append(stmt) if stmt.lstrip().upper().startswith("SELECT") else None)
    return _Controller(session), happy, selects


def _names(window):
    return sorted(t.track_name for t in window.base_track_view.loaded)


def test_direct_mode_loads_only_own_tracks_in_one_query(setup):
    controller, happy, selects = setup

    window = MoodTracksWindow(controller, happy)

    assert _names(window) == ["own", "shared"]
    assert len(selects) == 1
    assert window.track_count_label.text() == "Found 2 tracks"


def test_recursive_mode_includes_all_descendants_deduped_in_two_queries(setup):
    controller, happy, selects = setup
    window = MoodTracksWindow(controller, happy)
    selects.clear()

    window.toggle_recursive()

    assert _names(window) == ["child", "grandchild", "own", "shared"]
    assert len(selects) == 2  # one Mood query + one Track query
    assert window.track_count_label.text() == "Found 4 tracks (including all sub-moods)"
