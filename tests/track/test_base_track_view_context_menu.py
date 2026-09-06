"""Regression test for the BaseTrackView "Add to Playlist" / "Add to Mood"
context submenus.

They used to be a flat, unsorted list of every playlist/mood with no nesting
and no membership checkmarks -- unlike the player dock, which builds a nested,
alphabetically sorted tree with a check on the entries the track already
belongs to. Both now go through
``src.common.entity_submenu.populate_entity_submenu``.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
from src.track.base_track_view import BaseTrackView


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


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_playlist_submenu_is_hierarchical_and_sorted(qapp, session):
    rock = Playlist(playlist_name="Rock")
    session.add_all([rock, Playlist(playlist_name="Jazz")])
    session.flush()
    session.add(Playlist(playlist_name="Punk", parent_id=rock.playlist_id))
    track = Track(track_name="T")
    session.add(track)
    session.commit()

    view = BaseTrackView(_Controller(session), [track])
    view._populate_playlist_menu([track], [str(track.track_id)])

    rows = view.add_to_playlist_menu.actions()
    assert [a.text() for a in rows if not a.isSeparator()] == ["Jazz", "Rock"]
    rock_action = next(a for a in rows if a.text() == "Rock")
    assert rock_action.menu() is not None
    assert _texts(rock_action.menu()) == ["Punk", "Add to 'Rock'"]


def test_checkmark_reflects_selection_membership(qapp, session):
    both = Playlist(playlist_name="Both")
    one = Playlist(playlist_name="One")
    session.add_all([both, one])
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session.add_all([t1, t2])
    session.flush()
    session.add_all(
        [
            PlaylistTracks(playlist_id=both.playlist_id, track_id=t1.track_id, position=1),
            PlaylistTracks(playlist_id=both.playlist_id, track_id=t2.track_id, position=2),
            PlaylistTracks(playlist_id=one.playlist_id, track_id=t1.track_id, position=1),
        ]
    )
    session.commit()

    view = BaseTrackView(_Controller(session), [t1, t2])
    view._populate_playlist_menu([t1, t2], [str(t1.track_id), str(t2.track_id)])

    by_text = {a.text(): a for a in view.add_to_playlist_menu.actions() if not a.isSeparator()}
    assert by_text["Both"].isChecked()  # every selected track is in it
    assert not by_text["One (partial)"].isChecked()  # only t1 is in it
    assert by_text["One (partial)"].isCheckable()

    # Action payload still carries (entity_id, [track_id strings]) for the handler.
    assert by_text["Both"].data() == (both.playlist_id, [str(t1.track_id), str(t2.track_id)])


def test_membership_split_intersection_and_union(qapp, session):
    m_all = Mood(mood_name="All")
    m_some = Mood(mood_name="Some")
    session.add_all([m_all, m_some])
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session.add_all([t1, t2])
    session.flush()
    session.add_all(
        [
            MoodTrackAssociation(mood_id=m_all.mood_id, track_id=t1.track_id),
            MoodTrackAssociation(mood_id=m_all.mood_id, track_id=t2.track_id),
            MoodTrackAssociation(mood_id=m_some.mood_id, track_id=t1.track_id),
        ]
    )
    session.commit()

    full, partial = BaseTrackView._membership_split([t1, t2], "moods", "mood_id")
    assert full == {m_all.mood_id}
    assert partial == {m_some.mood_id}
