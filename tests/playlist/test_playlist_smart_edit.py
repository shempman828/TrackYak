"""Tests for SmartPlaylistEditDialog's auto-refresh persistence and
criteria-value validation, against a real in-memory sqlite session."""

from PySide6.QtWidgets import QDialog, QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.playlist import Playlist, SmartPlaylist
from src.playlist.playlist_smart_edit import SmartPlaylistEditDialog


@pytest.fixture(autouse=True)
def _no_blocking_message_boxes(monkeypatch):
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


def _select_no_value_operator(criteria_widget):
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
    _select_no_value_operator(dialog.criteria_widgets[0])

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
