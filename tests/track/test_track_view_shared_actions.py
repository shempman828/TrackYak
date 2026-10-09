"""Shared track-view helpers: delete, playlist/mood add, row selection, column reset and BaseTrackView drag."""

from types import SimpleNamespace

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QMessageBox, QTableView, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track
import src.track.view.base_track_view as base_track_view_module
from src.track.view.base_track_view import BaseTrackView
from src.track.view.track_columns import ColumnCustomizationDialog
from src.track.view.track_view_actions import TrackViewActionsMixin
import src.track.view.track_view_columns as track_view_columns_module
from src.track.view.track_view_editing import add_tracks_to_mood, add_tracks_to_playlist, delete_tracks_with_prompt, track_names_preview


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


class _FakeAppConfig:
    def __init__(self):
        self.visible, self.order, self.widths = [], [], []

    def get_track_view_visible_columns(self):
        return self.visible

    def set_track_view_visible_columns(self, value):
        self.visible = list(value)

    def get_track_view_column_order(self):
        return self.order

    def set_track_view_column_order(self, value):
        self.order = list(value)

    def get_track_view_column_widths(self):
        return self.widths

    def set_track_view_column_widths(self, value):
        self.widths = list(value)


@pytest.fixture(autouse=True)
def _isolated_column_config(monkeypatch):
    monkeypatch.setattr(track_view_columns_module, "app_config", _FakeAppConfig())


# ── delete_tracks_with_prompt ────────────────────────────────────────────────


class _Delete:
    def __init__(self, db_ok):
        self.db_ok = db_ok
        self.files = []

    def delete_entity(self, model_name, entity_ids=None, **_):
        return self.db_ok

    def delete_file(self, file_path=None):
        self.files.append(file_path)
        return True


def test_delete_keeps_files_when_db_delete_fails(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    controller = SimpleNamespace(delete=_Delete(db_ok=False))
    tracks = [SimpleNamespace(track_id=1, track_name="A", track_file_path="/x/a.mp3")]

    result = delete_tracks_with_prompt(QWidget(), controller, tracks, confirm=lambda *a: "db_and_file")

    assert result == []
    assert controller.delete.files == []
    assert warnings


def test_delete_removes_files_after_db_delete(qapp):
    controller = SimpleNamespace(delete=_Delete(db_ok=True))
    tracks = [SimpleNamespace(track_id=1, track_name="A", track_file_path="/x/a.mp3")]
    assert delete_tracks_with_prompt(QWidget(), controller, tracks, confirm=lambda *a: "db_and_file") == [1]
    assert controller.delete.files == ["/x/a.mp3"]


def test_delete_cancel_returns_none(qapp):
    controller = SimpleNamespace(delete=_Delete(db_ok=True))
    assert delete_tracks_with_prompt(QWidget(), controller, [SimpleNamespace(track_id=1, track_name="A")], confirm=lambda *a: None) is None


def test_names_preview_handles_missing_names():
    tracks = [SimpleNamespace(track_id=i, track_name=None if i == 2 else f"T{i}") for i in range(1, 6)]
    assert track_names_preview(tracks) == "T1, ID 2, T3 … and 2 more"


# ── playlist / mood add ──────────────────────────────────────────────────────


def test_add_to_playlist_appends_and_skips_members(session):
    controller = SimpleNamespace(get=GetFromDB(session), add=AddToDB(session))
    pl = Playlist(playlist_name="P")
    t1, t2, t3 = Track(track_name="1"), Track(track_name="2"), Track(track_name="3")
    session.add_all([pl, t1, t2, t3])
    session.commit()
    session.add(PlaylistTracks(playlist_id=pl.playlist_id, track_id=t1.track_id, position=1))
    session.commit()

    added, already, failed = add_tracks_to_playlist(controller, pl.playlist_id, [str(t1.track_id), str(t2.track_id), str(t3.track_id)])

    assert (added, already, failed) == (2, 1, 0)
    positions = {r.track_id: r.position for r in session.query(PlaylistTracks).all()}
    assert positions == {t1.track_id: 1, t2.track_id: 2, t3.track_id: 3}


def test_add_to_mood_skips_members(session):
    controller = SimpleNamespace(get=GetFromDB(session), add=AddToDB(session))
    mood = Mood(mood_name="Calm")
    t1, t2 = Track(track_name="1"), Track(track_name="2")
    session.add_all([mood, t1, t2])
    session.commit()
    session.add(MoodTrackAssociation(mood_id=mood.mood_id, track_id=t1.track_id))
    session.commit()

    assert add_tracks_to_mood(controller, mood.mood_id, [t1.track_id, t2.track_id]) == (1, 1, 0)
    assert session.query(MoodTrackAssociation).count() == 2


# ── TrackView row selection maps to the loaded list, no DB query ─────────────


class _Host(QWidget, TrackViewActionsMixin):
    def __init__(self, tracks):
        super().__init__()
        self._all_tracks = tracks
        self._filtered_tracks = []
        self._filter_active = False
        self.controller = SimpleNamespace(get=None)  # any DB access would raise
        self.model = QStandardItemModel(len(tracks), 1)
        for row, t in enumerate(tracks):
            self.model.setItem(row, 0, QStandardItem(t.track_name))
        self.table = QTableView()
        self.table.setModel(self.model)

    def _visible_source(self):
        return self._filtered_tracks if self._filter_active else self._all_tracks


def test_selected_track_objects_come_from_visible_list(qapp):
    tracks = [SimpleNamespace(track_id=i, track_name=f"T{i}") for i in range(5)]
    host = _Host(tracks)
    sel = host.table.selectionModel()
    for row in (3, 1):
        sel.select(host.model.index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert [t.track_id for t in host._get_selected_track_objects()] == [1, 3]


# ── column dialog ────────────────────────────────────────────────────────────


def _view(session, **kwargs):
    t = Track(track_name="T")
    session.add(t)
    session.commit()
    return BaseTrackView(SimpleNamespace(get=GetFromDB(session)), [t], **kwargs)


def test_column_reset_restores_view_defaults(qapp, session):
    view = _view(session)
    dialog = ColumnCustomizationDialog(view, view)
    dialog.reset_to_default()
    state = dialog.get_selected_state()

    assert state["order"] == list(view.columns)
    assert set(state["visible"]) == set(view.columns) - view.default_hidden_columns()
    assert "track_name" in state["visible"]


def test_column_dialog_refuses_to_hide_every_column(qapp, session, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    view = _view(session)
    dialog = ColumnCustomizationDialog(view, view)
    dialog.deselect_all()
    dialog.apply_changes()

    assert warnings
    assert any(not view.table.isColumnHidden(i) for i in range(len(view.columns)))


# ── BaseTrackView drag ───────────────────────────────────────────────────────


def test_base_view_drag_starts_on_table_with_selected_tracks(qapp, session, monkeypatch):
    dragged = []
    monkeypatch.setattr(base_track_view_module, "start_track_drag", lambda source, tracks: dragged.append((source, [t.track_id for t in tracks])))
    view = _view(session, enable_drag=True)
    view.table.selectRow(0)

    view.table.startDrag(None)

    assert dragged == [(view.table, [view._all_tracks[0].track_id])]
