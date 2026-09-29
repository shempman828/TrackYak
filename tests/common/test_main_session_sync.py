"""The Qt glue must run the main-session expire on the main thread, even when notified from a worker thread."""

import threading

from PySide6.QtCore import QObject

from src.common import main_session_sync
from src.db import db_session_sync


def test_worker_notification_runs_expire_on_main_thread(qapp, monkeypatch):
    monkeypatch.setattr(db_session_sync, "_notifier", None)
    ran_on = []
    monkeypatch.setattr(db_session_sync, "expire_main_session_if_stale", lambda: ran_on.append(threading.current_thread()))
    owner = QObject()
    try:
        main_session_sync.install_main_session_sync(owner)

        worker = threading.Thread(target=db_session_sync._notifier)
        worker.start()
        worker.join()

        assert ran_on == []  # queued, not run on the worker
        qapp.processEvents()
        assert ran_on == [threading.main_thread()]
    finally:
        owner.deleteLater()
