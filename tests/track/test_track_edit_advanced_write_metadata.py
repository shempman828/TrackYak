"""Tests for the Advanced tab's "Write Metadata to File" button: the per-track
sync loop runs in WriteMetadataWorker (off the UI thread), shows progress in
the status line, and the same button stops a running write.
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.track.edit import track_edit_advanced, write_metadata_worker
from src.track.edit.track_edit_advanced import STOP_WRITE_LABEL, WRITE_LABEL, AdvancedTab
from src.track.edit.write_metadata_worker import WriteMetadataWorker


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
