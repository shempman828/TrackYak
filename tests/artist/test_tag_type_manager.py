"""Tests for src/artist/tag_type_manager.py::TagTypeManagerDialog.

Covers docs/specs/artist_tags_tab.md AC2 (load order follows
sort_order/type_name, new types append at the end) and AC3 (Move Up/Down
renumbers and persists sort_order, and is a no-op at either end of the
list).

Runs against a real in-memory DB through the actual db_helpers (not fakes),
since ordering/persistence is exactly what's under test.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.artist.tag_type_manager import TagTypeManagerDialog
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, UpdateDB
from src.db.db_tables import TagType
from src.db.db_tables.base import Base


class _Controller:
    def __init__(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(engine)
        self.SessionFactory = scoped_session(sessionmaker(bind=engine))
        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)


def _make_tag_type(controller, name, sort_order=0):
    session = controller.SessionFactory
    tag_type = TagType(type_name=name, sort_order=sort_order)
    session.add(tag_type)
    session.commit()
    return tag_type


def _names_in_row_order(dialog):
    return [dialog._table.item(row, 0).text() for row in range(dialog._table.rowCount())]


def test_load_orders_by_sort_order_then_name(qapp):
    controller = _Controller()
    _make_tag_type(controller, "Religion", sort_order=1)
    _make_tag_type(controller, "Era", sort_order=0)
    _make_tag_type(controller, "Vibe", sort_order=0)

    dialog = TagTypeManagerDialog(controller)
    try:
        # both Era and Vibe share sort_order=0 -- alphabetical tiebreak --
        # and Religion's sort_order=1 puts it last despite sorting first
        # alphabetically.
        assert _names_in_row_order(dialog) == ["Era", "Vibe", "Religion"]
    finally:
        dialog.deleteLater()


def test_add_appends_new_type_at_the_end_regardless_of_name(qapp):
    controller = _Controller()
    _make_tag_type(controller, "Zeta", sort_order=0)
    _make_tag_type(controller, "Omega", sort_order=1)

    dialog = TagTypeManagerDialog(controller)
    try:
        session = controller.SessionFactory
        next_order = max(t.sort_order for t in dialog._current_types) + 1
        controller.add.add_entity("TagType", type_name="Alpha", sort_order=next_order)
        dialog._load()

        assert _names_in_row_order(dialog) == ["Zeta", "Omega", "Alpha"]
        session.expire_all()
        alpha = session.query(TagType).filter_by(type_name="Alpha").one()
        assert alpha.sort_order == 2
    finally:
        dialog.deleteLater()


def test_move_down_then_up_swaps_and_persists_sort_order(qapp):
    controller = _Controller()
    _make_tag_type(controller, "Era", sort_order=0)
    _make_tag_type(controller, "Religion", sort_order=1)
    _make_tag_type(controller, "Vibe", sort_order=2)

    dialog = TagTypeManagerDialog(controller)
    try:
        dialog._table.selectRow(0)  # Era
        dialog._move(1)  # Move Down

        assert _names_in_row_order(dialog) == ["Religion", "Era", "Vibe"]

        session = controller.SessionFactory
        session.expire_all()
        by_name = {t.type_name: t.sort_order for t in session.query(TagType).all()}
        assert by_name == {"Religion": 0, "Era": 1, "Vibe": 2}

        # Era is now selected at row 1 -- Move Up should swap it back.
        dialog._move(-1)
        assert _names_in_row_order(dialog) == ["Era", "Religion", "Vibe"]
    finally:
        dialog.deleteLater()


def test_move_up_at_top_and_down_at_bottom_are_no_ops(qapp):
    controller = _Controller()
    _make_tag_type(controller, "Era", sort_order=0)
    _make_tag_type(controller, "Vibe", sort_order=1)

    dialog = TagTypeManagerDialog(controller)
    try:
        dialog._table.selectRow(0)
        dialog._move(-1)
        assert _names_in_row_order(dialog) == ["Era", "Vibe"]

        dialog._table.selectRow(dialog._table.rowCount() - 1)
        dialog._move(1)
        assert _names_in_row_order(dialog) == ["Era", "Vibe"]
    finally:
        dialog.deleteLater()


def test_move_buttons_disabled_without_a_single_selection(qapp):
    controller = _Controller()
    _make_tag_type(controller, "Era", sort_order=0)
    _make_tag_type(controller, "Vibe", sort_order=1)

    dialog = TagTypeManagerDialog(controller)
    try:
        assert not dialog._move_up_btn.isEnabled()
        assert not dialog._move_down_btn.isEnabled()

        dialog._table.selectRow(1)
        assert dialog._move_up_btn.isEnabled()
        assert not dialog._move_down_btn.isEnabled()
    finally:
        dialog.deleteLater()
