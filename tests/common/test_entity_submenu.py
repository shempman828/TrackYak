"""Tests for the shared Add-to-Playlist / Add-to-Mood submenu builder.

The player dock and the base track view both feed their right-click
"Add to Playlist" / "Add to Mood" submenus through
``src.common.entity_submenu.populate_entity_submenu``. Previously each hand-rolled
its own copy and the base track view's had drifted into a flat, unsorted list
with no hierarchy and no membership checkmarks.
"""

from PySide6.QtWidgets import QMenu
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.entity_submenu import populate_entity_submenu
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood
from src.db.db_tables.playlist import Playlist


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
