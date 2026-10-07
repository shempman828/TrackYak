"""Tests for PlaylistRefreshController re-run queuing, startup coalescing and window reload."""

from types import SimpleNamespace
from typing import ClassVar

import pytest

import src.playlist.playlist_refresh_controller as module
from src.playlist.playlist_refresh_controller import PlaylistRefreshController


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeWorker:
    """Records starts; the test finishes it by emitting `finished`."""

    started: ClassVar[list] = []

    def __init__(self, builder, playlist_id, parent=None):
        self.playlist_id = playlist_id
        self.finished = _FakeSignal()

    def start(self):
        _FakeWorker.started.append(self)


class _FakeView:
    def __init__(self):
        self.controller = SimpleNamespace()
        self.reloads = 0
        self.reloaded_windows = []
        self.playlist_updated = _FakeSignal()

    def load_playlists(self):
        self.reloads += 1

    def reload_playlist_window(self, playlist_id):
        self.reloaded_windows.append(playlist_id)


@pytest.fixture
def controller(monkeypatch):
    _FakeWorker.started = []
    monkeypatch.setattr(module, "_SmartPlaylistRefreshWorker", _FakeWorker)
    monkeypatch.setattr(module, "show_status_message", lambda *a: None)
    return PlaylistRefreshController(_FakeView())


def test_refresh_requested_while_running_reruns_after(controller):
    results = []
    controller.start_refresh(7, lambda ok, pid: results.append(("first", ok)))
    controller.start_refresh(7, lambda ok, pid: results.append(("second", ok)))
    assert len(_FakeWorker.started) == 1

    _FakeWorker.started[0].finished.emit(True, 7)

    # The stale first result is skipped and a second run starts.
    assert results == []
    assert len(_FakeWorker.started) == 2

    _FakeWorker.started[1].finished.emit(True, 7)
    assert results == [("second", True)]


def test_finished_refresh_reloads_the_open_window(controller):
    controller.start_refresh(3, lambda ok, pid: None)

    _FakeWorker.started[0].finished.emit(True, 3)

    assert controller.view.reloaded_windows == [3]


def test_startup_refreshes_reload_the_tree_once(controller):
    controller.refresh_on_startup([1, 2, 3])

    for worker in list(_FakeWorker.started):
        worker.finished.emit(True, worker.playlist_id)

    assert controller.view.reloads == 1
