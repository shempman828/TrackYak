"""Regression tests for MetadataWriteDialog cancel handling and MetadataWriteWorker error handling."""

from types import SimpleNamespace

import pytest

from src.metadata.writers import metadata_writer_dialog as dialog_module
from src.metadata.writers.metadata_writer_dialog import MetadataWriteDialog, MetadataWriteWorker, WriteMode


class _FakeStatus:
    """Stand-in for StatusManager that records nothing."""

    @staticmethod
    def start_task(*_args, **_kwargs):
        pass

    @staticmethod
    def end_task(*_args, **_kwargs):
        pass

    @staticmethod
    def show_message(*_args, **_kwargs):
        pass


@pytest.fixture
def dialog(qapp, monkeypatch):
    monkeypatch.setattr(dialog_module, "MetadataWriter", lambda controller: SimpleNamespace(controller=controller))
    monkeypatch.setattr(dialog_module, "show_status_message", lambda *args, **kwargs: None)
    warnings = []
    monkeypatch.setattr(dialog_module.QMessageBox, "warning", lambda *args: warnings.append(args))
    dlg = MetadataWriteDialog(controller=None)
    dlg.status_manager = _FakeStatus
    dlg.warnings = warnings
    yield dlg
    dlg.deleteLater()


def test_write_worker_survives_unexpected_error_and_emits_finished(monkeypatch):
    monkeypatch.setattr(MetadataWriteWorker, "_release_db_session", staticmethod(lambda: None))

    def write(track_id, _mode):
        if track_id == 1:
            raise ValueError("boom")
        return True

    worker = MetadataWriteWorker(SimpleNamespace(write_metadata_to_track=write), [1, 2], WriteMode.UPDATE_EXISTING)
    received = []
    worker.finished.connect(received.append)
    worker.run()

    assert received == [{1: False, 2: True}]


def test_cancelled_scan_does_not_enable_update(dialog):
    dialog._cancelled = True
    dialog.on_scan_finished({1: True, 2: True})

    assert dialog.tracks_to_update == []
    assert not dialog.update_btn.isEnabled()
    assert dialog.scan_btn.isEnabled()


def test_cancelled_update_reports_cancel_not_success(dialog):
    dialog.tracks_to_update = [1, 2, 3]
    dialog._cancelled = True
    dialog.on_update_finished({1: True})

    assert "Cancelled - 1 of 3" in dialog.status_label.text()
    assert dialog.warnings == []
    assert dialog.tracks_to_update == []


def test_completed_update_with_failures_warns(dialog):
    dialog.tracks_to_update = [1, 2]
    dialog.on_update_finished({1: True, 2: False})

    assert len(dialog.warnings) == 1


def test_close_waits_for_running_worker(dialog):
    calls = []
    worker = SimpleNamespace(isRunning=lambda: True, cancel=lambda: calls.append("cancel"), wait=lambda *args: calls.append(("wait", args)))
    dialog.writer_thread = worker

    event = SimpleNamespace(accept=lambda: calls.append("accept"))
    dialog.closeEvent(event)

    assert calls == ["cancel", ("wait", ()), "accept"]
