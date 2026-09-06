"""
SyncItemsLoader — runs get_playlists()/get_moods() off the GUI thread and
must always hand back its pooled DB connection (scoped_session is thread-keyed;
a leaked read-only session pins a pool connection forever).
"""

from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication
import pytest

from src.common.cancellable_worker import CancellableWorker
from src.sync.sync_items_loader import SyncItemsLoader

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def no_real_session_release(monkeypatch):
    release = Mock()
    monkeypatch.setattr(CancellableWorker, "_release_db_session", staticmethod(release))
    return release


def _collect(signal):
    seen = []
    signal.connect(lambda *a: seen.append(a))
    return seen


def test_run_emits_loaded_and_releases_session(no_real_session_release):  # perf-AC11
    manager = Mock()
    manager.get_playlists.return_value = [{"kind": "playlist", "playlist_id": 1}]
    manager.get_moods.return_value = [{"kind": "mood", "mood_id": 2}]
    loader = SyncItemsLoader(manager)
    got = _collect(loader.loaded)

    loader.run()
    QCoreApplication.processEvents()

    assert got == [([{"kind": "playlist", "playlist_id": 1}], [{"kind": "mood", "mood_id": 2}])]
    no_real_session_release.assert_called_once()


def test_run_emits_failed_and_releases_session_on_error(no_real_session_release):  # perf-AC11/AC14
    manager = Mock()
    manager.get_playlists.side_effect = RuntimeError("db is locked")
    loader = SyncItemsLoader(manager)
    failed = _collect(loader.failed)
    loaded = _collect(loader.loaded)

    loader.run()
    QCoreApplication.processEvents()

    assert loaded == []
    assert failed == [("db is locked",)]
    no_real_session_release.assert_called_once()


def test_cancelled_run_emits_nothing_but_still_releases(no_real_session_release):  # perf-AC11
    manager = Mock()
    manager.get_playlists.return_value = []
    manager.get_moods.return_value = []
    loader = SyncItemsLoader(manager)
    loaded = _collect(loader.loaded)
    loader.request_cancel()

    loader.run()
    QCoreApplication.processEvents()

    assert loaded == []
    no_real_session_release.assert_called_once()
