"""
Regression tests for MtpListWorker.

Motivating bug: SyncView enumerated MTP devices synchronously on the GUI
thread (on open and every 5s), so a wedged `gio` froze the UI. MtpListWorker
moves list_devices() onto a throwaway QThread and hands the result back via
the `ready` signal.
"""

import threading
import time

from PySide6.QtCore import QCoreApplication

from src.sync.mtp_list_worker import MtpListWorker


class _FakeMtp:
    def __init__(self, devices=None, raises=False, delay=0.0):
        self._devices = devices or []
        self._raises = raises
        self._delay = delay
        self.called_on_thread = None

    def list_devices(self):
        self.called_on_thread = threading.current_thread()
        if self._delay:
            time.sleep(self._delay)
        if self._raises:
            raise RuntimeError("boom")
        return self._devices


def _drain(worker, qapp, timeout_ms=5000):
    """Wait for the worker to finish and flush its queued `ready` signal."""
    assert worker.wait(timeout_ms)
    for _ in range(50):
        QCoreApplication.processEvents()
        time.sleep(0.01)


def test_emits_device_list_off_the_gui_thread(qapp):
    sentinel = [object(), object()]
    fake = _FakeMtp(devices=sentinel)
    worker = MtpListWorker(fake)

    received = []
    worker.ready.connect(received.append)
    worker.start()
    _drain(worker, qapp)

    assert received == [sentinel]
    assert fake.called_on_thread is not threading.main_thread()


def test_emits_empty_list_when_list_devices_raises(qapp):
    worker = MtpListWorker(_FakeMtp(raises=True))

    received = []
    worker.ready.connect(received.append)
    worker.start()
    _drain(worker, qapp)

    assert received == [[]]


def test_cancelled_worker_does_not_emit(qapp):
    worker = MtpListWorker(_FakeMtp(devices=[object()], delay=0.2))

    received = []
    worker.ready.connect(received.append)
    worker.start()
    worker.request_cancel()
    _drain(worker, qapp)

    assert received == []
