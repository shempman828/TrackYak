"""Tests for the "Delete Track(s)" button on the track edit dialog's
Advanced tab (docs/specs/add_delete_track_button_to_track_edit_advanced_tab.md).
Each test maps 1:1 to a numbered acceptance criterion in that spec.

The button reuses the shared confirm_delete_with_file_option prompt and the
controller.delete.delete_entity / delete_file helpers, then closes the owning
dialog so the parent view reloads.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track import track_edit_advanced
from src.track.track_edit_advanced import AdvancedTab


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


class _RecordingDelete:
    def __init__(self, entity_ok=True, file_ok=True):
        self.entity_ok = entity_ok
        self.file_ok = file_ok
        self.entity_calls = []
        self.file_calls = []

    def delete_entity(self, model_name, entity_ids=None, **filters):
        self.entity_calls.append((model_name, entity_ids, filters))
        return self.entity_ok

    def delete_file(self, file_path=None, **kwargs):
        self.file_calls.append(file_path)
        return self.file_ok


class _Controller:
    def __init__(self, entity_ok=True, file_ok=True):
        self.delete = _RecordingDelete(entity_ok, file_ok)


class _FakeDialog:
    def __init__(self):
        self.accepted_count = 0

    def accept(self):
        self.accepted_count += 1


def _make_tab(tracks, controller, dialog):
    tab = AdvancedTab(tracks, controller, dialog=dialog)
    tab.load(tracks)
    return tab


def _patch_choice(monkeypatch, value):
    """Force the shared confirm dialog to return `value` without showing UI."""
    monkeypatch.setattr(
        track_edit_advanced, "confirm_delete_with_file_option", lambda *a, **k: value
    )


# AC1 -----------------------------------------------------------------------
def test_button_present_and_labelled_for_single_track(qapp, session):
    track = _make_track(session)
    tab = _make_tab([track], _Controller(), _FakeDialog())
    assert tab._delete_btn.text() == "Delete Track"
    assert tab._delete_btn.property("danger") is True


# AC1 -----------------------------------------------------------------------
def test_button_labelled_plural_for_multi_track(qapp, session):
    t1 = _make_track(session, track_name="One")
    t2 = _make_track(session, track_name="Two")
    tab = _make_tab([t1, t2], _Controller(), _FakeDialog())
    assert tab._delete_btn.text() == "Delete Tracks"


# AC2 -----------------------------------------------------------------------
def test_cancel_makes_no_changes_and_leaves_dialog_open(qapp, session, monkeypatch):
    track = _make_track(session, track_file_path="/music/a.flac")
    controller = _Controller()
    dialog = _FakeDialog()
    tab = _make_tab([track], controller, dialog)

    _patch_choice(monkeypatch, None)
    tab._on_delete()

    assert controller.delete.entity_calls == []
    assert controller.delete.file_calls == []
    assert dialog.accepted_count == 0


# AC3 -----------------------------------------------------------------------
def test_remove_from_library_deletes_db_row_only_and_closes(qapp, session, monkeypatch):
    t1 = _make_track(session, track_name="One", track_file_path="/music/a.flac")
    t2 = _make_track(session, track_name="Two", track_file_path="/music/b.flac")
    controller = _Controller()
    dialog = _FakeDialog()
    tab = _make_tab([t1, t2], controller, dialog)

    _patch_choice(monkeypatch, "db_only")
    tab._on_delete()

    assert controller.delete.entity_calls == [("Track", [t1.track_id, t2.track_id], {})]
    assert controller.delete.file_calls == []
    assert dialog.accepted_count == 1


# AC4 -----------------------------------------------------------------------
def test_delete_files_too_removes_db_rows_and_each_file_then_closes(qapp, session, monkeypatch):
    t1 = _make_track(session, track_name="One", track_file_path="/music/a.flac")
    t2 = _make_track(session, track_name="Two", track_file_path="/music/b.flac")
    controller = _Controller()
    dialog = _FakeDialog()
    tab = _make_tab([t1, t2], controller, dialog)

    _patch_choice(monkeypatch, "db_and_file")
    tab._on_delete()

    assert controller.delete.entity_calls == [("Track", [t1.track_id, t2.track_id], {})]
    assert controller.delete.file_calls == ["/music/a.flac", "/music/b.flac"]
    assert dialog.accepted_count == 1


# AC5 -----------------------------------------------------------------------
def test_delete_files_too_skips_tracks_with_no_file_path(qapp, session, monkeypatch):
    t1 = _make_track(session, track_name="One", track_file_path="/music/a.flac")
    t2 = _make_track(session, track_name="Two", track_file_path=None)
    controller = _Controller()
    tab = _make_tab([t1, t2], controller, _FakeDialog())

    _patch_choice(monkeypatch, "db_and_file")
    tab._on_delete()

    assert controller.delete.file_calls == ["/music/a.flac"]


# AC6 -----------------------------------------------------------------------
def test_failed_db_delete_does_not_close_dialog_or_touch_files(qapp, session, monkeypatch):
    track = _make_track(session, track_file_path="/music/a.flac")
    controller = _Controller(entity_ok=False)
    dialog = _FakeDialog()
    tab = _make_tab([track], controller, dialog)

    _patch_choice(monkeypatch, "db_and_file")
    monkeypatch.setattr(track_edit_advanced.QMessageBox, "warning", lambda *a, **k: None)
    tab._on_delete()

    assert controller.delete.file_calls == []
    assert dialog.accepted_count == 0


# AC7 -----------------------------------------------------------------------
def test_falls_back_to_window_close_when_no_dialog_ref(qapp, session, monkeypatch):
    track = _make_track(session)
    controller = _Controller()
    tab = _make_tab([track], controller, None)

    closed = []
    fake_window = type("W", (), {"close": lambda self: closed.append(True)})()
    monkeypatch.setattr(tab, "window", lambda: fake_window)

    _patch_choice(monkeypatch, "db_only")
    tab._on_delete()

    assert controller.delete.entity_calls == [("Track", [track.track_id], {})]
    assert closed == [True]
