"""GUI close, refresh, reset-layout and screen-placement behavior, tested on lightweight stand-ins."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

from src.core.main_window import GUI
from src.foundation.config_setup import app_config


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args):
            self.calls.append(name)

        return method


class _CloseHost(QMainWindow):
    """Main window with only the attributes closeEvent touches; no player or queue docks."""

    _stop_explicit_recalc_worker = GUI._stop_explicit_recalc_worker
    _close_miniplayer = GUI._close_miniplayer

    def __init__(self):
        super().__init__()
        self.mediaplayer = _Recorder()
        self.mediaplayer.queue_manager = _Recorder()
        self.controller = _Recorder()


def test_close_event_survives_missing_docks_and_stops_recalc_worker(qapp, monkeypatch):
    monkeypatch.setattr(app_config, "save", lambda: None)
    host = _CloseHost()

    class _Worker:
        cancelled = waited = False

        def request_cancel(self):
            self.cancelled = True

        def wait(self):
            self.waited = True

    worker = _Worker()
    host._explicit_recalc_worker = worker

    GUI.closeEvent(host, None)

    assert worker.cancelled and worker.waited
    assert host._explicit_recalc_worker is None
    assert "cleanup" in host.mediaplayer.calls
    assert "close_session" in host.controller.calls


def test_close_event_shuts_down_built_views_that_own_workers(qapp, monkeypatch):
    monkeypatch.setattr(app_config, "save", lambda: None)
    host = _CloseHost()

    class _View:
        stopped = False

        def shutdown(self):
            self.stopped = True

    view = _View()
    host._view_cache = {"Sync": view, "Plain": QWidget()}

    GUI.closeEvent(host, None)

    assert view.stopped


def test_refresh_all_views_queries_only_entities_of_built_views(qapp):
    from src.album.view.album_view import AlbumView

    queried = []

    class _Get:
        def get_all_entities(self, name):
            queried.append(name)
            return [name]

    class _Controller:
        get = _Get()

    album = AlbumView.__new__(AlbumView)  # bypass the heavy constructor; only isinstance + load_data matter
    loaded = []
    album.load_data = loaded.append

    class _Host:
        def __init__(self):
            self.controller = _Controller()
            self._view_cache = {"Albums": album}

    GUI._refresh_all_views(_Host())

    assert queried == ["Album"]
    assert loaded == [["Album"]]


def test_reset_layout_keeps_queue_hidden_when_it_was_hidden(qapp, monkeypatch):
    host = QMainWindow()
    host.queue_dock = QDockWidget("Queue", host)
    host.queue_dock.setWidget(QWidget())
    host.addDockWidget(Qt.RightDockWidgetArea, host.queue_dock)
    host.queue_dock.hide()
    host.player_ui = object()
    visible_calls = []
    host.set_queue_visible = visible_calls.append

    GUI._reset_ui_layout(host)

    assert visible_calls == [False]


def test_ensure_window_in_screen_leaves_maximized_window_alone(qapp):
    moves = []

    class _Host:
        def isMaximized(self):
            return True

        def isFullScreen(self):
            return False

        def geometry(self):
            return QRect(-5000, -5000, 100, 100)

        def move(self, *a):
            moves.append(a)

    GUI.ensure_window_in_screen(_Host())

    assert moves == []
