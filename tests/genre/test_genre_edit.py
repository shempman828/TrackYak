"""Tests for src/genre/genre_edit.py: get_valid_parents' full-depth descendant
exclusion, find_duplicate_genre_name's case-insensitive matching, and
GenreEditDialog populating its parent combo for a brand-new genre as well as
an existing one.
"""

import pytest

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.genre import Genre
from src.genre.genre_edit import GenreEditDialog, find_duplicate_genre_name, get_valid_parents


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller(session):
    return _Controller(session)


def _make_genre(session, name, parent=None):
    genre = Genre(genre_name=name, parent=parent)
    session.add(genre)
    session.commit()
    return genre


def test_get_valid_parents_with_no_genre_returns_all_genres_sorted(session, controller):
    _make_genre(session, "Rock")
    _make_genre(session, "Alt")

    valid = get_valid_parents(controller, None)

    assert [g.genre_name for g in valid] == ["Alt", "Rock"]


def test_get_valid_parents_excludes_grandchild_not_just_direct_child(session, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    hardcore = _make_genre(session, "Hardcore", parent=punk)

    valid_ids = {g.genre_id for g in get_valid_parents(controller, rock)}

    # Punk (direct child) and Hardcore (grandchild) must both be excluded --
    # picking either as Rock's parent would create a cycle.
    assert punk.genre_id not in valid_ids
    assert hardcore.genre_id not in valid_ids
    assert rock.genre_id not in valid_ids


def test_get_valid_parents_includes_unrelated_genres(session, controller):
    rock = _make_genre(session, "Rock")
    jazz = _make_genre(session, "Jazz")

    valid_ids = {g.genre_id for g in get_valid_parents(controller, rock)}

    assert jazz.genre_id in valid_ids


def test_find_duplicate_genre_name_is_case_insensitive(session, controller):
    _make_genre(session, "Rock")

    assert find_duplicate_genre_name(controller, "rock") is not None
    assert find_duplicate_genre_name(controller, "ROCK") is not None
    assert find_duplicate_genre_name(controller, "Jazz") is None


def test_find_duplicate_genre_name_excludes_given_id(session, controller):
    rock = _make_genre(session, "Rock")

    assert find_duplicate_genre_name(controller, "Rock", exclude_id=rock.genre_id) is None


def test_new_genre_dialog_offers_existing_genres_as_parent(session, qapp, controller):
    _make_genre(session, "Rock")

    dialog = GenreEditDialog(controller, None)

    assert dialog.parent_combo.count() == 2
    assert dialog.parent_combo.itemText(1) == "Rock"


def test_edit_genre_dialog_still_excludes_descendants_from_parent_combo(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)

    dialog = GenreEditDialog(controller, rock)

    combo_names = [dialog.parent_combo.itemText(i) for i in range(dialog.parent_combo.count())]
    assert punk.genre_name not in combo_names


def test_new_genre_can_be_saved_with_a_chosen_parent(session, qapp, controller):
    rock = _make_genre(session, "Rock")

    dialog = GenreEditDialog(controller, None)
    dialog.name_input.setText("Punk")
    idx = dialog.parent_combo.findData(rock.genre_id)
    dialog.parent_combo.setCurrentIndex(idx)
    dialog.validate()

    assert dialog.result() == 1  # QDialog.Accepted
    assert dialog.result_genre.parent_id == rock.genre_id


def test_duplicate_name_is_rejected_case_insensitively(session, qapp, controller, monkeypatch):
    _make_genre(session, "Rock")
    warnings = []
    monkeypatch.setattr("src.genre.genre_edit.QMessageBox.warning", lambda *args: warnings.append(args[-1]))

    dialog = GenreEditDialog(controller, None)
    dialog.name_input.setText("rock")
    dialog.validate()

    assert dialog.result() == 0  # not accepted
    assert warnings == ["Genre name already exists"]
