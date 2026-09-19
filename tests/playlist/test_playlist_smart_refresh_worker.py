"""Regression test: _SmartPlaylistRefreshWorker.run() only caught
SQLAlchemyError inside SmartPlaylistBuilder.refresh_playlist(); any other
exception in the worker thread meant `finished` never fired. Since
PlaylistView keys its in-flight-refresh guard off that signal, a playlist
whose refresh hit an unexpected error would stay marked "refreshing"
forever, blocking any future refresh until the app restarted.
"""

from src.playlist.playlist_refresh_controller import _SmartPlaylistRefreshWorker


class _FakeBuilder:
    def __init__(self, exc):
        self._exc = exc

    def refresh_playlist(self, playlist_id):
        raise self._exc


def test_unexpected_exception_still_emits_finished(qapp):
    worker = _SmartPlaylistRefreshWorker(_FakeBuilder(RuntimeError("boom")), playlist_id=42)
    received = []
    worker.finished.connect(lambda success, pid: received.append((success, pid)))

    worker.run()

    assert received == [(False, 42)]
