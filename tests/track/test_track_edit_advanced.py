"""Track edit Advanced tab: Delete Track(s) and Write Metadata to File."""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track.edit import track_edit_advanced, write_metadata_worker
from src.track.edit.track_edit_advanced import STOP_WRITE_LABEL, WRITE_LABEL, AdvancedTab
from src.track.edit.write_metadata_worker import WriteMetadataWorker

# ---------------------------------------------------------------------------
# Tests for the "Delete Track(s)" button on the track edit dialog's
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests for the Advanced tab's "Write Metadata to File" button: the per-track
# ---------------------------------------------------------------------------


def _ok(changed=()):
    return {"success": True, "changed": list(changed), "message": "Updated" if changed else "Already up to date"}


class _FakeWriter:
    """Stands in for MetadataWriter; records ids and runs an optional hook per call."""

    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def sync_metadata_to_track(self, track_id):
        self.calls.append(track_id)
        if self.on_call:
            self.on_call(track_id)
        return _ok(["TITLE"])


@pytest.fixture
def no_db_release(monkeypatch):
    monkeypatch.setattr(WriteMetadataWorker, "_release_db_session", staticmethod(lambda: None))


def _run_worker(monkeypatch, tracks, on_call=None):
    """Run the worker's loop on this thread and capture its signals."""
    writer = _FakeWriter()
    monkeypatch.setattr(write_metadata_worker, "MetadataWriter", lambda controller: writer)
    worker = WriteMetadataWorker(object(), tracks)
    if on_call:
        writer.on_call = lambda track_id: on_call(worker, track_id)
    progress, results = [], []
    worker.progress.connect(lambda done, total: progress.append((done, total)))
    worker.results_ready.connect(lambda r, cancelled: results.append((r, cancelled)))
    worker.run()
    return writer, progress, results


def test_worker_syncs_every_track_and_reports_progress(qapp, monkeypatch, no_db_release):
    writer, progress, results = _run_worker(monkeypatch, [(1, "One"), (2, "Two"), (3, "Three")])

    assert writer.calls == [1, 2, 3]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert len(results) == 1
    rows, cancelled = results[0]
    assert [name for name, _ in rows] == ["One", "Two", "Three"]
    assert cancelled is False


def test_worker_stops_between_tracks_on_cancel(qapp, monkeypatch, no_db_release):
    writer, _progress, results = _run_worker(monkeypatch, [(1, "One"), (2, "Two"), (3, "Three")], on_call=lambda worker, _id: worker.request_cancel())

    assert writer.calls == [1]
    rows, cancelled = results[0]
    assert len(rows) == 1
    assert cancelled is True


class _FakeWorker:
    """Records start/cancel without starting a thread."""

    instances: ClassVar[list] = []

    def __init__(self, controller, tracks, parent=None):
        self.tracks = tracks
        self.started = False
        self.is_cancelled = False
        self.progress = SimpleNamespace(connect=lambda slot: None, disconnect=lambda slot: None)
        self.results_ready = SimpleNamespace(connect=lambda slot: None, disconnect=lambda slot: None)
        self.error = SimpleNamespace(connect=lambda slot: None, disconnect=lambda slot: None)
        _FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def request_cancel(self):
        self.is_cancelled = True

    def wait(self):
        return True

    def deleteLater(self):
        pass


@pytest.fixture
def tab(qapp, monkeypatch):
    _FakeWorker.instances = []
    monkeypatch.setattr(track_edit_advanced, "WriteMetadataWorker", _FakeWorker)
    monkeypatch.setattr(track_edit_advanced, "show_status_message", lambda *a, **k: None)
    tracks = [SimpleNamespace(track_id=i, track_name=f"T{i}") for i in (1, 2, 3)]
    return AdvancedTab(tracks, controller=None)


def test_click_starts_worker_without_running_loop_on_ui_thread(tab):
    tab._on_write_metadata()

    worker = _FakeWorker.instances[0]
    assert worker.started is True
    assert worker.tracks == [(1, "T1"), (2, "T2"), (3, "T3")]
    assert tab._write_btn.text() == STOP_WRITE_LABEL
    assert tab._delete_btn.isEnabled() is False


def test_second_click_requests_stop(tab):
    tab._on_write_metadata()
    tab._on_write_metadata()

    assert len(_FakeWorker.instances) == 1
    assert _FakeWorker.instances[0].is_cancelled is True
    assert tab._write_btn.isEnabled() is False


def test_stopped_results_restore_buttons_and_report_partial_count(tab):
    tab._on_write_metadata()
    tab._on_write_results([("T1", _ok(["TITLE"]))], True)

    assert tab._write_worker is None
    assert tab._write_btn.text() == WRITE_LABEL
    assert tab._write_btn.isEnabled() is True
    assert tab._delete_btn.isEnabled() is True
    assert tab._status_label.text().startswith("Write Metadata stopped after 1 / 3 track(s): 1 updated")


def test_cleanup_cancels_running_write(tab):
    tab._on_write_metadata()
    worker = _FakeWorker.instances[0]

    tab.cleanup()

    assert worker.is_cancelled is True
    assert tab._write_worker is None
