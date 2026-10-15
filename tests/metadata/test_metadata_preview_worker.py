"""
Regression test for MetadataPreviewWorker.

Motivating bug: the write dialog's "Preview only" mode logged only a file
count, never which tags would change. The preview worker now runs
MetadataWriter.get_changed_tags per eligible track and logs the changed keys.
Runs the worker on a real QThread so the int-keyed result dict crosses the
thread boundary as it does in the dialog.
"""

import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from src.metadata.writers.metadata_writer_dialog import MetadataPreviewWorker


def _drain(worker, timeout_ms=5000):
    assert worker.wait(timeout_ms)
    for _ in range(50):
        QCoreApplication.processEvents()
        time.sleep(0.01)


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
