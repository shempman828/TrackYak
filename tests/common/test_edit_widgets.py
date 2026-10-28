"""Small shared edit helpers: dirty-check, optional int field, rating stars, split and lookup dialogs."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.edit_dirty import value_changed
from src.common.widgets.optional_int_edit import OptionalIntEdit
from src.common.widgets.rating_widget import RatingStarsWidget

# ---- value_changed ----------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [(None, "", False), (None, "   ", False), ("", None, False), (None, 0, False), ("Rock", " Rock ", False), ("Rock", "Jazz", True), (None, "Jazz", True), ("Rock", None, True)],
)
def test_value_changed(old, new, expected):
    assert value_changed(old, new) is expected


# ---- OptionalIntEdit --------------------------------------------------------


def test_optional_int_edit_reads_intermediate_text_as_none(qapp):
    edit = OptionalIntEdit(min_value=-10, max_value=10)
    edit.setText("-")

    assert edit.get_value_or_none() is None

    edit.setText("-4")
    assert edit.get_value_or_none() == -4


# ---- RatingStarsWidget ------------------------------------------------------


def test_rating_widget_keyboard_steps_and_clamps(qapp):
    widget = RatingStarsWidget()
    emitted = []
    widget.rating_changed.connect(emitted.append)

    QTest.keyClick(widget, Qt.Key_Right)
    QTest.keyClick(widget, Qt.Key_Right)
    QTest.keyClick(widget, Qt.Key_Left)
    QTest.keyClick(widget, Qt.Key_End)
    QTest.keyClick(widget, Qt.Key_Right)  # already at 10: no emit
    QTest.keyClick(widget, Qt.Key_Home)

    assert emitted == [0.5, 1.0, 0.5, 10.0, 0.0]
    assert widget.focusPolicy() == Qt.StrongFocus


def test_rating_widget_set_rating_updates_accessible_text_without_emitting(qapp):
    widget = RatingStarsWidget()
    emitted = []
    widget.rating_changed.connect(emitted.append)

    widget.set_rating(7.5)

    assert emitted == []
    assert widget.accessibleDescription() == "7.5 of 10"


# ---- SplitDBDialog ----------------------------------------------------------


class _StubSplit:
    def split_entity(self, *args):
        return True


def _split_dialog(name):
    from src.common.dialogs.base_split_dialog import SplitDBDialog

    return SplitDBDialog(_StubSplit(), "Genre", SimpleNamespace(genre_id=1, genre_name=name))


def test_split_dialog_validates_on_open(qapp):
    dialog = _split_dialog("Rock")  # no delimiter: two blank rows
    try:
        assert not dialog.split_btn.isEnabled()
        assert dialog.warning_label.text()
    finally:
        dialog.deleteLater()


def test_split_dialog_removes_the_row_being_edited(qapp):
    dialog = _split_dialog("Rock / Jazz / Blues")
    try:
        dialog.show()
        jazz_edit = dialog.split_list.itemWidget(dialog.split_list.item(1))
        jazz_edit.setFocus()
        qapp.processEvents()

        dialog._remove_selected_row()

        assert dialog._collect_split_names() == ["Rock", "Blues"]
    finally:
        dialog.close()
        dialog.deleteLater()


# ---- BaseLookupManagerDialog duplicate check --------------------------------


@pytest.fixture
def session():
    from src.db.db_tables.base import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_lookup_name_check_ignores_case(qapp, session):
    from src.common.dialogs.lookup_manager_dialog import BaseLookupManagerDialog
    from src.db.db_helpers.get import GetFromDB
    from src.db.db_tables.genre import Genre

    session.add(Genre(genre_name="Rock"))
    session.commit()

    class _Fake:
        _ENTITY_TYPE = "Genre"
        _ID_ATTR = "genre_id"
        _NAME_ATTR = "genre_name"
        _ENTITY_LABEL = "genre"
        _name_taken = BaseLookupManagerDialog._name_taken
        controller = SimpleNamespace(get=GetFromDB(session))

    rock_id = session.query(Genre).one().genre_id
    assert _Fake()._name_taken("rock") is True
    assert _Fake()._name_taken("ROCK", exclude_id=rock_id) is False
    assert _Fake()._name_taken("Jazz") is False
