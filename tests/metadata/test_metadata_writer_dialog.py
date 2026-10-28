"""Write Metadata flow: the writer dialog and worker, the scanner and
preview workers, and status broadcasting from write_metadata_to_track.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QCoreApplication
import pytest

from src.metadata.writers import metadata_writer_dialog as dialog_module
from src.metadata.writers.metadata_writer import MetadataWriter, WriteMode
from src.metadata.writers.metadata_writer_dialog import MetadataPreviewWorker, MetadataScannerWorker, MetadataWriteDialog, MetadataWriteWorker

# ---------------------------------------------------------------------------
# Regression tests for MetadataWriteDialog cancel handling and MetadataWriteWorker error handling.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression: write_metadata_to_file/write_metadata_to_track must not
# ---------------------------------------------------------------------------


def _make_writer(tmp_path, monkeypatch, exists: bool = True):
    file_path = tmp_path / "song.mp3"
    if exists:
        file_path.write_bytes(b"ID3")

    controller = MagicMock()
    track = MagicMock(track_file_path=str(file_path))
    controller.get.get_entity_object.return_value = track

    writer = MetadataWriter(controller)
    monkeypatch.setattr(writer.track_data, "get_track_data", lambda track_id: {"track": {"track_id": track_id}})
    monkeypatch.setattr(writer, "_write_id3", lambda *a, **k: True)
    return writer, file_path


def test_write_metadata_to_track_does_not_broadcast_status(tmp_path, monkeypatch):
    writer, _ = _make_writer(tmp_path, monkeypatch)
    mock_status = MagicMock()
    monkeypatch.setattr("src.foundation.status_utility.StatusManager.show_message", mock_status)

    result = writer.write_metadata_to_track(1, WriteMode.UPDATE_EXISTING)

    assert result is True
    mock_status.assert_not_called()


def test_write_metadata_to_track_missing_file_does_not_broadcast_status(tmp_path, monkeypatch):
    writer, _ = _make_writer(tmp_path, monkeypatch, exists=False)
    mock_status = MagicMock()
    monkeypatch.setattr("src.foundation.status_utility.StatusManager.show_message", mock_status)

    result = writer.write_metadata_to_track(1, WriteMode.UPDATE_EXISTING)

    assert result is False
    mock_status.assert_not_called()


# ---------------------------------------------------------------------------
# Regression test for MetadataScannerWorker.finished.
# ---------------------------------------------------------------------------


def _drain(worker, timeout_ms=5000):
    assert worker.wait(timeout_ms)
    for _ in range(50):
        QCoreApplication.processEvents()
        time.sleep(0.01)


def _fake_metadata_writer(tracks):
    controller = SimpleNamespace(get=SimpleNamespace(get_all_entities=lambda entity, **filters: tracks))
    return SimpleNamespace(controller=controller)


def test_finished_carries_int_keyed_dict_across_thread(qapp, monkeypatch):
    monkeypatch.setattr(MetadataScannerWorker, "_release_db_session", staticmethod(lambda: None))
    tracks = [SimpleNamespace(track_id=1, track_file_path=__file__), SimpleNamespace(track_id=2, track_file_path="/no/such/file.mp3")]
    worker = MetadataScannerWorker(_fake_metadata_writer(tracks))

    received = []
    worker.finished.connect(received.append)
    worker.start()
    _drain(worker)

    assert received == [{1: True, 2: False}]
    assert isinstance(received[0], dict)


# ---------------------------------------------------------------------------
# Regression test for MetadataPreviewWorker.
# ---------------------------------------------------------------------------


class _FakeWriter:
    def __init__(self, paths, changes):
        tracks = {tid: SimpleNamespace(track_id=tid, track_file_path=path) for tid, path in paths.items()}
        self.controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda entity, track_id: tracks.get(track_id)))
        self._changes = changes

    def get_changed_tags(self, track_id, file_path):
        result = self._changes[track_id]
        if isinstance(result, Exception):
            raise result
        return result


def test_logs_changed_tag_keys_per_file(qapp, monkeypatch):
    monkeypatch.setattr(MetadataPreviewWorker, "_release_db_session", staticmethod(lambda: None))
    writer = _FakeWriter(paths={1: "/music/a.mp3", 2: "/music/b.flac", 3: "/music/c.ogg"}, changes={1: ["TALB", "TIT2"], 2: [], 3: OSError("unreadable")})
    worker = MetadataPreviewWorker(writer, [1, 2, 3])

    logs, received = [], []
    worker.log_message.connect(logs.append)
    worker.finished.connect(received.append)
    worker.start()
    _drain(worker)

    assert received == [{1: ["TALB", "TIT2"], 2: [], 3: None}]
    assert "~ a.mp3: TALB, TIT2" in logs
    assert "= b.flac: no changes" in logs
    assert any("Error reading track 3" in line for line in logs)
