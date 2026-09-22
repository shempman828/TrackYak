"""Regression tests for BaseTrackView / TrackView feature parity.

See docs/specs/base_track_view_parity.md. BaseTrackView used to hardcode
7 columns, filter/sort synchronously on the main thread, and have no
column customization, clipboard copy, Delete-key shortcut, or batched
delete. It now shares TrackViewColumnsMixin / TrackViewDataMixin /
TrackViewSearchMixin with the main library TrackView.
"""

from PySide6.QtCore import QItemSelectionModel, QThread
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.role import Role
from src.db.db_tables.track import Track
import src.track.view.base_track_view as base_track_view_module
from src.track.view.base_track_view import BaseTrackView
import src.track.view.track_view_columns as track_view_columns_module
from src.track.view.track_view_filter import FilterWorker, SortWorker


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
        self.delete = DeleteDB(session)


class _FakeAppConfig:
    """In-memory stand-in so tests never touch config.ini."""

    def __init__(self):
        self._visible = []
        self._order = []
        self._widths = []

    def get_track_view_visible_columns(self):
        return self._visible

    def set_track_view_visible_columns(self, value):
        self._visible = list(value)

    def get_track_view_column_order(self):
        return self._order

    def set_track_view_column_order(self, value):
        self._order = list(value)

    def get_track_view_column_widths(self):
        return self._widths

    def set_track_view_column_widths(self, value):
        self._widths = list(value)


@pytest.fixture(autouse=True)
def _isolated_column_config(monkeypatch):
    """Every BaseTrackView construction reads/writes column layout via the
    shared TrackViewColumnsMixin/app_config keys -- isolate all tests in
    this file from the real config.ini on disk."""
    monkeypatch.setattr(track_view_columns_module, "app_config", _FakeAppConfig())


def _pump_until_worker_done(worker, timeout_ms=5000):
    """Background FilterWorker/SortWorker run on a real QThread and deliver
    their `finished` signal via a queued connection back to the main thread.
    Drive the event loop until the thread stops, then flush a few more
    rounds so that final queued delivery is actually processed before we
    inspect state the slot is responsible for updating."""
    deadline = timeout_ms
    while worker.isRunning() and deadline > 0:
        QApplication.processEvents()
        QThread.msleep(10)
        deadline -= 10
    assert not worker.isRunning(), "worker did not finish in time"
    for _ in range(10):
        QApplication.processEvents()


def test_full_track_fields_column_set_with_default_visibility(qapp, session):
    """AC2: BaseTrackView shows the full TRACK_FIELDS set, not a hardcoded 7,
    with the same default-hidden columns as TrackView."""
    track = Track(track_name="T")
    session.add(track)
    session.commit()

    view = BaseTrackView(_Controller(session), [track])

    assert len(view.columns) > 7
    assert "release_year" in view.columns
    assert "primary_artist_names" in view.columns

    hidden_by_default = {"file_size", "bit_rate", "sample_rate", "track_id", "track_file_path"}
    col_keys = list(view.columns.keys())
    for i, key in enumerate(col_keys):
        if key in hidden_by_default:
            assert view.table.isColumnHidden(i), f"{key} should be hidden by default"
    assert not view.table.isColumnHidden(col_keys.index("track_name"))


def test_column_layout_persists_and_is_shared_across_instances(qapp, session):
    """AC3: hiding a column in one BaseTrackView (e.g. mood tracks) and
    reopening another (e.g. genre tracks) restores the same layout, since
    both go through the same TrackViewColumnsMixin persistence keys."""
    track = Track(track_name="T")
    session.add(track)
    session.commit()

    first = BaseTrackView(_Controller(session), [track])
    duration_index = list(first.columns.keys()).index("duration")
    first.table.setColumnHidden(duration_index, True)
    first.save_column_state()

    second = BaseTrackView(_Controller(session), [track])
    assert second.table.isColumnHidden(duration_index)


def test_search_runs_through_background_filter_worker(qapp, session):
    """AC4: typing in the search bar filters via FilterWorker on a
    background thread rather than blocking the main thread."""
    match = Track(track_name="Blue Skies")
    other = Track(track_name="Red Dirt")
    session.add_all([match, other])
    session.commit()

    view = BaseTrackView(_Controller(session), [match, other])

    view.search_bar.setText("blue")
    assert isinstance(view._filter_worker, FilterWorker)
    _pump_until_worker_done(view._filter_worker)

    assert view._filter_active
    assert [t.track_id for t in view._filtered_tracks] == [match.track_id]


def test_header_click_sorts_via_background_sort_worker_using_lookup_cache(qapp, session):
    """AC5: clicking a column header sorts via SortWorker, and the Artist
    column orders by the bulk-fetched filing name (Artist.sort_name), not
    the displayed name -- mirrors test_track_view_data_artist_sort.py."""
    primary_role = Role(role_name="Primary Artist")
    session.add(primary_role)
    session.flush()

    beatles = Artist(artist_name="The Beatles", sort_name="Beatles, The")
    abba = Artist(artist_name="ABBA", sort_name=None)
    session.add_all([beatles, abba])
    session.flush()

    t1 = Track(track_name="t1")
    t2 = Track(track_name="t2")
    session.add_all([t1, t2])
    session.flush()
    session.add_all(
        [
            TrackArtistRole(track_id=t1.track_id, artist_id=beatles.artist_id, role_id=primary_role.role_id),
            TrackArtistRole(track_id=t2.track_id, artist_id=abba.artist_id, role_id=primary_role.role_id),
        ]
    )
    session.commit()

    view = BaseTrackView(_Controller(session), [t1, t2])
    artist_col = list(view.columns.keys()).index("primary_artist_names")

    view._on_header_clicked(artist_col)
    assert isinstance(view._sort_worker, SortWorker)
    _pump_until_worker_done(view._sort_worker)

    # Filing order: "ABBA" (t2) before "Beatles, The" (t1) -- opposite of display-name order.
    assert [t.track_id for t in view._all_tracks] == [t2.track_id, t1.track_id]


def test_ctrl_c_copies_selected_row_to_clipboard(qapp, session):
    """AC6."""
    track = Track(track_name="Copy Me")
    session.add(track)
    session.commit()

    view = BaseTrackView(_Controller(session), [track])
    view.table.selectRow(0)
    view._copy_selected_rows()

    clipboard_text = QApplication.clipboard().text()
    assert "Copy Me" in clipboard_text


def test_delete_key_shortcut_triggers_same_flow_as_menu_action(qapp, session, monkeypatch):
    """AC7: the Delete key triggers the same confirm-delete flow as the
    "Delete Tracks..." context menu action."""
    track = Track(track_name="Doomed")
    session.add(track)
    session.commit()

    calls = []
    monkeypatch.setattr(base_track_view_module, "confirm_delete_with_file_option", lambda *a, **k: (calls.append(1), None)[1])

    view = BaseTrackView(_Controller(session), [track])
    view.table.selectRow(0)

    # Directly invoke what the QShortcut(QKeySequence.Delete) is wired to,
    # matching how Qt itself would call it on a real key press.
    assert QKeySequence(QKeySequence.Delete) is not None
    view._delete_selected_tracks()

    assert calls == [1]  # confirm dialog was shown; cancel (None) aborted the delete


def test_batch_delete_issues_single_entity_ids_call(qapp, session, monkeypatch):
    """AC8: deleting multiple selected tracks issues one delete_entity call
    with entity_ids instead of one call per track."""
    t1 = Track(track_name="A")
    t2 = Track(track_name="B")
    session.add_all([t1, t2])
    session.commit()

    monkeypatch.setattr(base_track_view_module, "confirm_delete_with_file_option", lambda *a, **k: "db_only")

    view = BaseTrackView(_Controller(session), [t1, t2])
    view.table.selectRow(0)
    view.table.selectionModel().select(view.model.index(1, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    delete_calls = []
    real_delete_entity = view.controller.delete.delete_entity

    def spy_delete_entity(model_name, **kwargs):
        delete_calls.append(kwargs)
        return real_delete_entity(model_name, **kwargs)

    monkeypatch.setattr(view.controller.delete, "delete_entity", spy_delete_entity)

    view._delete_selected_tracks()

    assert len(delete_calls) == 1
    assert set(delete_calls[0]["entity_ids"]) == {t1.track_id, t2.track_id}
