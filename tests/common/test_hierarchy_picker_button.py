"""Tests for HierarchyPickerButton: the searchable, hierarchy-nested
single-select popup that replaces a flat QComboBox for parent_id-shaped
choices (Genre's parent picker today)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.widgets.hierarchy_picker_button import HierarchyPickerButton
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _button(session):
    return HierarchyPickerButton(_Controller(session), "Genre", "genre_id", "genre_name")


def _find_submenu(menu, title):
    for action in menu.actions():
        if action.menu() is not None and action.text() == title:
            return action.menu()
    raise AssertionError(f"no submenu titled {title!r} in {[a.text() for a in menu.actions()]}")


def _find_leaf(menu, title):
    for action in menu.actions():
        if action.menu() is None and action.text() == title:
            return action
    raise AssertionError(f"no leaf titled {title!r} in {[a.text() for a in menu.actions()]}")


def test_button_label_defaults_to_none_label_then_reflects_selection(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    session.add(rock)
    session.commit()

    button = _button(session)
    assert button.text() == "(No parent)"

    button.set_options([rock])
    button.set_selected_id(rock.genre_id)

    assert button.text() == "Rock"


def test_popup_order_is_search_then_none_label_then_nested_tree(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    session.add(rock)
    session.flush()
    session.add(Genre(genre_name="Punk", parent=rock))
    jazz = Genre(genre_name="Jazz")
    session.add(jazz)
    session.commit()

    button = _button(session)
    button.set_options(session.query(Genre).all())

    actions = button._menu.actions()
    assert actions[0] is button._completer_action
    assert actions[1].isSeparator()
    assert actions[2].text() == "(No parent)"
    assert actions[3].isSeparator()
    # Root genres nested by parent_id, alphabetized: "Jazz" (leaf) before "Rock" (branch).
    rest = [(a.text(), a.menu() is not None) for a in actions[4:]]
    assert rest == [("Jazz", False), ("Rock", True)]


def test_typing_and_picking_a_completion_selects_and_closes(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    session.add(rock)
    session.commit()

    button = _button(session)
    button.set_options([rock])
    received = []
    button.selection_changed.connect(lambda: received.append(button.selected_id()))

    button._search_edit._completer.activated.emit("Rock")

    assert button.selected_id() == rock.genre_id
    assert button.text() == "Rock"
    assert received == [rock.genre_id]
    assert not button._menu.isVisible()


def test_clicking_a_nested_submenu_leaf_selects_and_closes(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    session.add(rock)
    session.flush()
    punk = Genre(genre_name="Punk", parent=rock)
    session.add(punk)
    session.commit()

    button = _button(session)
    button.set_options(session.query(Genre).all())

    rock_menu = _find_submenu(button._menu, "Rock")
    _find_leaf(rock_menu, "Punk").trigger()

    assert button.selected_id() == punk.genre_id
    assert button.text() == "Punk"


def test_clicking_top_level_leaf_selects_and_closes(qapp):
    session = _session()
    jazz = Genre(genre_name="Jazz")
    session.add(jazz)
    session.commit()

    button = _button(session)
    button.set_options([jazz])

    _find_leaf(button._menu, "Jazz").trigger()

    assert button.selected_id() == jazz.genre_id
    assert button.text() == "Jazz"


def test_clicking_none_label_clears_selection(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    session.add(rock)
    session.commit()

    button = _button(session)
    button.set_options([rock])
    button.set_selected_id(rock.genre_id)

    _find_leaf(button._menu, "(No parent)").trigger()

    assert button.selected_id() is None
    assert button.text() == "(No parent)"


def test_set_options_rebuilds_from_given_list_only_and_excludes_others(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    jazz = Genre(genre_name="Jazz")
    session.add_all([rock, jazz])
    session.commit()

    button = _button(session)
    button.set_options([rock])  # deliberately omit jazz

    assert button.available_ids() == [rock.genre_id]
    names = [a.text() for a in button._menu.actions() if a.menu() is None and not a.isSeparator()]
    assert "Jazz" not in names
    # No completion offered for the excluded entity either.
    assert "Jazz" not in button._search_edit._display_to_id


def test_set_selected_id_ignores_ids_outside_current_options(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    jazz = Genre(genre_name="Jazz")
    session.add_all([rock, jazz])
    session.commit()

    button = _button(session)
    button.set_options([rock])

    button.set_selected_id(jazz.genre_id)

    assert button.selected_id() is None
    assert button.text() == "(No parent)"


def test_selection_survives_set_options_when_still_present_and_resets_when_not(qapp):
    session = _session()
    rock = Genre(genre_name="Rock")
    jazz = Genre(genre_name="Jazz")
    session.add_all([rock, jazz])
    session.commit()

    button = _button(session)
    button.set_options([rock, jazz])
    button.set_selected_id(rock.genre_id)

    button.set_options([rock, jazz])
    assert button.selected_id() == rock.genre_id

    button.set_options([jazz])
    assert button.selected_id() is None
    assert button.text() == "(No parent)"
