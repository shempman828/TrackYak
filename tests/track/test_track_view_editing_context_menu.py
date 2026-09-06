"""Regression tests for the main library Tracks tab right-click submenus.

``TrackViewEditingMixin`` used to hand-roll its own mood submenu (keyed off a
nonexistent ``parent_mood_id``, so never nested) and had no "Add to Playlist"
submenu at all. Both now go through ``src.common.entity_submenu``.
"""

from PySide6.QtWidgets import QMenu, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.track.track_view_editing import TrackViewEditingMixin


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


class _Host(QWidget, TrackViewEditingMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_playlist_submenu_now_exists_and_is_hierarchical(qapp, session):
    rock = Playlist(playlist_name="Rock")
    session.add_all([rock, Playlist(playlist_name="Jazz")])
    session.flush()
    session.add(Playlist(playlist_name="Punk", parent_id=rock.playlist_id))
    track = Track(track_name="T")
    session.add(track)
    session.commit()

    host = _Host(_Controller(session))
    menu = QMenu()
    host._populate_playlist_submenu(menu, [track], [str(track.track_id)])

    assert _texts(menu) == ["Jazz", "Rock"]
    rock_action = next(a for a in menu.actions() if a.text() == "Rock")
    assert rock_action.menu() is not None
    assert _texts(rock_action.menu()) == ["Punk", "Add to 'Rock'"]


def test_mood_submenu_nests_off_real_parent_id(qapp, session):
    calm = Mood(mood_name="Calm")
    session.add(calm)
    session.flush()
    session.add(Mood(mood_name="Serene", parent_id=calm.mood_id))
    track = Track(track_name="T")
    session.add(track)
    session.commit()

    host = _Host(_Controller(session))
    menu = QMenu()
    host._populate_mood_submenu(menu, [track], [str(track.track_id)])

    calm_action = next(a for a in menu.actions() if a.text() == "Calm")
    assert calm_action.menu() is not None
    assert _texts(calm_action.menu()) == ["Serene", "Add to 'Calm'"]


def test_checkmarks_reflect_multi_track_membership(qapp, session):
    both = Mood(mood_name="Both")
    one = Mood(mood_name="One")
    session.add_all([both, one])
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session.add_all([t1, t2])
    session.flush()
    session.add_all(
        [
            MoodTrackAssociation(mood_id=both.mood_id, track_id=t1.track_id),
            MoodTrackAssociation(mood_id=both.mood_id, track_id=t2.track_id),
            MoodTrackAssociation(mood_id=one.mood_id, track_id=t1.track_id),
        ]
    )
    session.commit()

    host = _Host(_Controller(session))
    menu = QMenu()
    host._populate_mood_submenu(menu, [t1, t2], [str(t1.track_id), str(t2.track_id)])

    by_text = {a.text(): a for a in menu.actions() if not a.isSeparator()}
    assert by_text["Both"].isChecked()
    assert by_text["One (partial)"].isCheckable()
    assert not by_text["One (partial)"].isChecked()


def test_add_to_playlist_handler_adds_links_for_whole_selection(qapp, session):
    pl = Playlist(playlist_name="Target")
    session.add(pl)
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session.add_all([t1, t2])
    session.commit()

    host = _Host(_Controller(session))
    menu = QMenu()
    host._populate_playlist_submenu(menu, [t1, t2], [str(t1.track_id), str(t2.track_id)])
    menu.actions()[0].trigger()  # "Target"

    links = session.query(PlaylistTracks).filter_by(playlist_id=pl.playlist_id).all()
    assert {lnk.track_id for lnk in links} == {t1.track_id, t2.track_id}
    assert sorted(lnk.position for lnk in links) == [1, 2]
