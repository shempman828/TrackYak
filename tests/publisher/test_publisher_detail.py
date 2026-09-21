"""Regression test: editing an album from the publisher view's "View Albums"
popup did not update the publisher detail panel's "Albums: N" count (or the
publisher tree's per-node count) until the user navigated away and back.
PublisherAlbumsWindow.albums_changed must be wired through PublisherDetailTab
to refresh the displayed count.
"""

from types import SimpleNamespace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog

import src.publisher.publisher_detail as publisher_detail_module
from src.publisher.publisher_detail import PublisherDetailTab


class _StubAlbumsWindow(QDialog):
    """Stands in for PublisherAlbumsWindow without touching the database."""

    albums_changed = Signal()

    def __init__(self, controller, publisher, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.publisher = publisher


class _StubGet:
    def __init__(self, publisher, album_ids):
        self._publisher = publisher
        self.album_ids = album_ids

    def get_entity_object(self, model_name, **kwargs):
        return self._publisher

    def get_all_entities(self, model_name, **kwargs):
        return []


class _StubController:
    def __init__(self, publisher, album_ids):
        self.get = _StubGet(publisher, album_ids)


def _make_publisher():
    return SimpleNamespace(publisher_id=1, publisher_name="Test Publisher", second_pass=False, first_pass=False, is_active=1, begin_year=None, end_year=None, description="", logo_path=None)


def test_editing_album_refreshes_publisher_album_count(monkeypatch, qapp):
    publisher = _make_publisher()
    album_ids = [1]
    controller = _StubController(publisher, album_ids)

    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: list(album_ids))
    monkeypatch.setattr(publisher_detail_module, "PublisherAlbumsWindow", _StubAlbumsWindow)

    tab = PublisherDetailTab(controller)
    try:
        tab.load_publisher_data(1)
        assert tab.tracks_label.text() == "Albums: 1"

        tab._open_albums_window()

        # Simulate an album being newly associated with this publisher
        # while the (still-open) albums popup edits it.
        album_ids.append(2)

        received = []
        tab.albums_changed.connect(lambda: received.append(True))
        tab._albums_window.albums_changed.emit()

        assert tab.tracks_label.text() == "Albums: 2"
        assert tab.associations_btn.text() == "View Albums (2)"
        assert received == [True]
    finally:
        tab.deleteLater()
