"""Tests for the shared Add-to-Playlist / Add-to-Mood submenu builder.

The player dock, the base track view and the main library Tracks tab all feed
their right-click "Add to Playlist" / "Add to Mood" submenus through
``src.common.entity_submenu``. Previously each hand-rolled its own copy and they
drifted (the base track view's was a flat unsorted list; the Tracks tab keyed
nesting off a ``parent_mood_id`` attribute that does not exist).
"""

from PySide6.QtWidgets import QMenu
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.entity_submenu import populate_entity_submenu, selection_membership
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)()


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _rows(menu):
    """Flatten a QMenu into (text, is_submenu, checkable, checked, data) tuples."""
    out = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        out.append(
            (
                action.text(),
                action.menu() is not None,
                action.isCheckable(),
                action.isChecked(),
                action.data(),
            )
        )
    return out


def _submenu(menu, title):
    for action in menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no submenu titled {title!r} in {[a.text() for a in menu.actions()]}")


def test_hierarchy_is_nested_and_alphabetically_sorted(qapp, session):
    rock = Playlist(playlist_name="Rock")
    jazz = Playlist(playlist_name="Jazz")
    session.add_all([rock, jazz])
    session.flush()
    session.add_all(
        [
            Playlist(playlist_name="Zeppelin", parent_id=rock.playlist_id),
            Playlist(playlist_name="AC/DC", parent_id=rock.playlist_id),
        ]
    )
    session.commit()

    menu = QMenu()
    populate_entity_submenu(
        menu, controller=_Controller(session), entity_type="Playlist", on_trigger=lambda *_: None
    )

    top = [(text, is_sub) for text, is_sub, *_ in _rows(menu)]
    # "Jazz" (leaf) sorts before "Rock" (submenu), per-level alphabetical.
    assert top == [("Jazz", False), ("Rock", True)]

    rock_menu = _submenu(menu, "Rock")
    rock_rows = [text for text, *_ in _rows(rock_menu)]
    assert rock_rows == ["AC/DC", "Zeppelin", "Add to 'Rock'"]


def test_smart_playlists_are_excluded(qapp, session):
    session.add_all([Playlist(playlist_name="Manual"), Playlist(playlist_name="Auto", is_smart=1)])
    session.commit()

    menu = QMenu()
    populate_entity_submenu(
        menu, controller=_Controller(session), entity_type="Playlist", on_trigger=lambda *_: None
    )

    assert [text for text, *_ in _rows(menu)] == ["Manual"]


def test_member_ids_checked_and_partial_ids_suffixed(qapp, session):
    a = Playlist(playlist_name="All")
    s = Playlist(playlist_name="Some")
    n = Playlist(playlist_name="None")
    session.add_all([a, s, n])
    session.commit()

    menu = QMenu()
    populate_entity_submenu(
        menu,
        controller=_Controller(session),
        entity_type="Playlist",
        on_trigger=lambda *_: None,
        member_ids={a.playlist_id},
        partial_ids={s.playlist_id},
    )

    by_text = {text: (checkable, checked) for text, _, checkable, checked, _ in _rows(menu)}
    assert by_text["All"] == (True, True)
    assert by_text["Some (partial)"] == (True, False)
    assert by_text["None"] == (False, False)


def test_action_data_and_trigger_wiring(qapp, session):
    session.add(Playlist(playlist_name="Target"))
    session.commit()
    target_id = session.query(Playlist).one().playlist_id

    fired = []
    menu = QMenu()
    populate_entity_submenu(
        menu,
        controller=_Controller(session),
        entity_type="Playlist",
        on_trigger=lambda *_: fired.append(menu.actions()[0].data()),
        make_action_data=lambda entity_id: (entity_id, ["7", "8"]),
    )

    action = menu.actions()[0]
    assert action.data() == (target_id, ["7", "8"])
    action.trigger()
    assert fired == [(target_id, ["7", "8"])]


def test_empty_and_moods(qapp, session):
    empty = QMenu()
    populate_entity_submenu(
        empty, controller=_Controller(session), entity_type="Playlist", on_trigger=lambda *_: None
    )
    only = empty.actions()[0]
    assert only.text() == "No playlists available"
    assert not only.isEnabled()

    session.add_all([Mood(mood_name="Wistful"), Mood(mood_name="Angry")])
    session.commit()
    mood_menu = QMenu()
    populate_entity_submenu(
        mood_menu, controller=_Controller(session), entity_type="Mood", on_trigger=lambda *_: None
    )
    assert [text for text, *_ in _rows(mood_menu)] == ["Angry", "Wistful"]


def test_nesting_uses_parent_id_not_parent_mood_id(qapp, session):
    """The Tracks tab's old copy looked for ``parent_mood_id`` (nonexistent), so
    it never nested. The shared builder must nest off the real ``parent_id``."""
    calm = Mood(mood_name="Calm")
    session.add(calm)
    session.flush()
    session.add(Mood(mood_name="Serene", parent_id=calm.mood_id))
    session.commit()

    menu = QMenu()
    populate_entity_submenu(
        menu, controller=_Controller(session), entity_type="Mood", on_trigger=lambda *_: None
    )
    calm_menu = _submenu(menu, "Calm")
    assert [text for text, *_ in _rows(calm_menu)] == ["Serene", "Add to 'Calm'"]


def test_selection_membership_intersection_and_union(qapp, session):
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

    full, partial = selection_membership([t1, t2], "moods", "mood_id")
    assert full == {m_all.mood_id}
    assert partial == {m_some.mood_id}

    # Empty selection -> empty sets, no crash.
    assert selection_membership([], "playlists", "playlist_id") == (set(), set())


def test_selection_membership_playlists(qapp, session):
    p = Playlist(playlist_name="P")
    session.add(p)
    t1 = Track(track_name="T1")
    t2 = Track(track_name="T2")
    session.add_all([t1, t2])
    session.flush()
    session.add(PlaylistTracks(playlist_id=p.playlist_id, track_id=t1.track_id, position=1))
    session.commit()

    full, partial = selection_membership([t1, t2], "playlists", "playlist_id")
    assert full == set()
    assert partial == {p.playlist_id}
