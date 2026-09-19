"""Regression test for the publisher album view: double-clicking an album
in PublisherAlbumsWindow must open the AlbumEditor for that album (the
underlying ScrollableAlbumFlow.albumDoubleClicked signal already existed
but was never connected)."""

from types import SimpleNamespace
from typing import ClassVar

import src.publisher.publisher_albums as publisher_albums_module
from src.publisher.publisher_albums import PublisherAlbumsWindow


class _StubAlbumEditor:
    """Stands in for AlbumEditor so the test doesn't need a modal loop."""

    instances: ClassVar[list] = []

    def __init__(self, controller, album, parent=None):
        self.controller = controller
        self.album = album
        self.parent = parent
        self.exec_calls = 0
        _StubAlbumEditor.instances.append(self)

    def exec(self):
        self.exec_calls += 1


class _StubGet:
    def __init__(self, album):
        self._album = album
        self.calls = []

    def get_entity_object(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self._album


class _StubController:
    def __init__(self, album):
        self.get = _StubGet(album)


def _make_publisher():
    return SimpleNamespace(publisher_id=1, publisher_name="Test Publisher")


def test_double_click_opens_album_editor(monkeypatch, qapp):
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubController(album)

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", lambda *a, **k: [])

    window = PublisherAlbumsWindow(controller, _make_publisher())
    try:
        window._open_album_editor(album)

        assert len(_StubAlbumEditor.instances) == 1
        editor = _StubAlbumEditor.instances[0]
        assert editor.album is album
        assert editor.parent is window
        assert editor.exec_calls == 1
        assert controller.get.calls == [("Album", {"album_id": 7})]
    finally:
        window.deleteLater()


def test_double_click_refreshes_album_list_after_editor_closes(monkeypatch, qapp):
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubController(album)

    load_calls = []

    def _fake_get_publisher_albums(*a, **k):
        load_calls.append(True)
        return []

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", _fake_get_publisher_albums)

    window = PublisherAlbumsWindow(controller, _make_publisher())
    try:
        assert len(load_calls) == 1  # initial load on construction

        window._open_album_editor(album)

        assert len(load_calls) == 2  # reloaded after editor closed
    finally:
        window.deleteLater()


def test_flow_double_click_signal_is_connected_to_editor(monkeypatch, qapp):
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubController(album)

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", lambda *a, **k: [])

    window = PublisherAlbumsWindow(controller, _make_publisher())
    try:
        window.flow.albumDoubleClicked.emit(album)

        assert len(_StubAlbumEditor.instances) == 1
        assert _StubAlbumEditor.instances[0].album is album
    finally:
        window.deleteLater()
