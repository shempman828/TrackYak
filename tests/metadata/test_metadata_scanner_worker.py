"""
Regression test for MetadataScannerWorker.finished.

Motivating bug: `finished` was declared as `Signal(dict)`. Emitted from the
worker thread, that queued connection makes PySide marshal the payload as a
QVariantMap, which only supports string keys -- but the payload is
{track_id: bool} with int keys, so the marshal failed with
"_pythonToCppCopy: Cannot copy-convert ... (dict) to C++" and the result
never reached on_scan_finished. Must actually run the worker on a real
QThread (not call .run() directly) to exercise the cross-thread marshaling.
"""

import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from src.metadata.writers.metadata_writer_dialog import MetadataScannerWorker


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
