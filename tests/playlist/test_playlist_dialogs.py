"""Playlist create/edit dialogs (plain and smart): validate-before-accept,
pre-filled values, auto-refresh persistence, and the date-range widget.
"""

from types import SimpleNamespace

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, SmartPlaylist
from src.playlist.playlist_edit import EditPlaylist
from src.playlist.playlist_new import PlaylistCreateDialog
from src.playlist.smart.playlist_smart_criteria_widget import _DateRangeEdit
from src.playlist.smart.playlist_smart_edit import SmartPlaylistEditDialog
from src.playlist.smart.playlist_smart_new import SmartPlaylistCreateDialog

# ---------------------------------------------------------------------------
# Tests for PlaylistCreateDialog's validate-before-accept behavior.
# ---------------------------------------------------------------------------


def test_blank_name_does_not_accept(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    dialog = PlaylistCreateDialog()
    dialog.name_input.setText("   ")
    dialog.desc_input.setPlainText("some text")

    dialog._validate_and_accept()

    assert dialog.result() != QDialog.Accepted


def test_valid_name_accepts(qapp):
    dialog = PlaylistCreateDialog()
    dialog.name_input.setText("My Playlist")

    dialog._validate_and_accept()

    assert dialog.result() == QDialog.Accepted


def test_ok_button_is_labeled_create(qapp):
    dialog = PlaylistCreateDialog()

    assert dialog.buttons.button(QDialogButtonBox.Ok).text() == "Create"


# ---------------------------------------------------------------------------
# Regression test for EditPlaylist pre-filling the wrong attribute.
# ---------------------------------------------------------------------------


class _FakeController:
    pass


def test_description_field_is_prefilled_from_playlist_description(qapp):
    playlist = SimpleNamespace(
        playlist_id=1,
        playlist_name="My Playlist",
        playlist_description="Some existing description",
        is_smart=False,
    )

    dialog = EditPlaylist(_FakeController(), playlist)

    assert dialog.desc_edit.toPlainText() == "Some existing description"


# ---------------------------------------------------------------------------
# Tests for SmartPlaylistCreateDialog's validate-before-accept behavior.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_blocking_message_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def _select_no_value_operator(criteria_widget):
    """Pick an operator ("is empty"/"has a value") that needs no input,
    so the row is valid regardless of which field type it starts on."""
    combo = criteria_widget.operator_combo
    for i in range(combo.count()):
        if combo.itemData(i) in ("isnull", "notnull"):
            combo.setCurrentIndex(i)
            return
    raise AssertionError("no no-value operator found")


def test_smart_blank_name_does_not_accept(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("   ")
    _select_no_value_operator(dialog.criteria_widgets[0])

    dialog._on_ok_clicked()

    assert dialog.result() != QDialog.Accepted


def test_criteria_row_missing_value_does_not_accept(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("My Smart Playlist")
    # Default row is left with its default (blank) value and an operator
    # that needs one.

    dialog._on_ok_clicked()

    assert dialog.result() != QDialog.Accepted


def test_valid_form_accepts_and_returns_auto_refresh(qapp):
    dialog = SmartPlaylistCreateDialog()
    dialog.name_edit.setText("My Smart Playlist")
    _select_no_value_operator(dialog.criteria_widgets[0])
    dialog.auto_refresh_check.setChecked(True)

    dialog._on_ok_clicked()

    assert dialog.result() == QDialog.Accepted
    name, _description, _logic, _criteria, auto_refresh = dialog.get_data()
    assert name == "My Smart Playlist"
    assert auto_refresh is True


# ---------------------------------------------------------------------------
# Tests for SmartPlaylistEditDialog's auto-refresh persistence and
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_blocking_message_boxes_edit(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


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


def _make_smart_playlist(session, auto_refresh=0):
    playlist = Playlist(playlist_name="Test Smart", is_smart=1)
    session.add(playlist)
    session.commit()
    smart = SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND", auto_refresh=auto_refresh)
    session.add(smart)
    session.commit()
    return playlist


def test_auto_refresh_checkbox_prefilled_from_existing_value(qapp, session, controller):
    playlist = _make_smart_playlist(session, auto_refresh=1)

    dialog = SmartPlaylistEditDialog(controller, playlist.playlist_id)

    assert dialog.auto_refresh_check.isChecked() is True


def _select_no_value_operator_edit(criteria_widget):
    combo = criteria_widget.operator_combo
    for i in range(combo.count()):
        if combo.itemData(i) in ("isnull", "notnull"):
            combo.setCurrentIndex(i)
            return
    raise AssertionError("no no-value operator found")


def test_saving_persists_auto_refresh_flag(qapp, session, controller):
    playlist = _make_smart_playlist(session, auto_refresh=0)
    dialog = SmartPlaylistEditDialog(controller, playlist.playlist_id)
    dialog.auto_refresh_check.setChecked(True)
    # The dialog fell back to one blank criteria row (no saved criteria
    # existed yet) -- give it a valid, value-free operator so validation
    # doesn't block the save this test is checking.
    _select_no_value_operator_edit(dialog.criteria_widgets[0])

    dialog._on_ok_clicked()

    session.expire_all()
    smart = session.get(SmartPlaylist, playlist.playlist_id)
    assert smart.auto_refresh == 1
    assert dialog.result() == QDialog.Accepted


def test_criteria_row_missing_value_blocks_save(qapp, session, controller):
    playlist = _make_smart_playlist(session)
    dialog = SmartPlaylistEditDialog(controller, playlist.playlist_id)
    # The fallback blank row's default operator needs a value it doesn't have.

    dialog._on_ok_clicked()

    assert dialog.result() != QDialog.Accepted


# ---------------------------------------------------------------------------
# Regression test for _DateRangeEdit swapping an inverted date range.
# ---------------------------------------------------------------------------


def test_inverted_range_is_swapped(qapp):
    widget = _DateRangeEdit()
    widget.start_edit.setDate(QDate(2026, 6, 1))
    widget.end_edit.setDate(QDate(2026, 1, 1))

    value = widget.get_value()

    assert value == "2026-01-01 00:00:00|2026-06-01 23:59:59"


def test_normal_range_is_unchanged(qapp):
    widget = _DateRangeEdit()
    widget.start_edit.setDate(QDate(2026, 1, 1))
    widget.end_edit.setDate(QDate(2026, 6, 1))

    value = widget.get_value()

    assert value == "2026-01-01 00:00:00|2026-06-01 23:59:59"


def _select_operator(widget, operator):
    combo = widget.operator_combo
    combo.setCurrentIndex(combo.findData(operator))


def test_numeric_between_uses_a_two_bound_range(qapp):
    from src.playlist.smart.playlist_smart_criteria_widget import CriteriaWidget, _NumberRangeEdit

    widget = CriteriaWidget()
    widget.set_criteria({"field": "bpm", "comparison": "range", "value": "140|90", "type": "Integer"})

    assert isinstance(widget.value_widget, _NumberRangeEdit)
    assert widget.get_criteria()["value"] == "90|140"

    _select_operator(widget, "gt")
    assert not isinstance(widget.value_widget, _NumberRangeEdit)


def test_integer_value_is_restored_from_stored_text(qapp):
    from src.playlist.smart.playlist_smart_criteria_widget import CriteriaWidget

    widget = CriteriaWidget()
    widget.set_criteria({"field": "bpm", "comparison": "gt", "value": "120", "type": "Integer"})

    assert widget.get_criteria()["value"] == 120


def test_last_criteria_row_cannot_be_deleted(qapp):
    from src.playlist.smart.playlist_smart_new import SmartPlaylistCreateDialog

    dialog = SmartPlaylistCreateDialog()
    assert not dialog.criteria_widgets[0].delete_btn.isEnabled()

    dialog.add_criteria_widget()
    assert all(w.delete_btn.isEnabled() for w in dialog.criteria_widgets)

    dialog.remove_criteria_widget(dialog.criteria_widgets[1])
    assert not dialog.criteria_widgets[0].delete_btn.isEnabled()
