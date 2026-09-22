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
    return SimpleNamespace(publisher_id=1, publisher_name="Test Publisher", second_pass=False, first_pass=False, is_active=1, begin_year=None, end_year=None, description="")


def test_editing_album_refreshes_publisher_album_count(monkeypatch, qapp):
    publisher = _make_publisher()
    album_ids = [1]
    controller = _StubController(publisher, album_ids)

    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: list(album_ids))
    monkeypatch.setattr(publisher_detail_module, "PublisherAlbumsWindow", _StubAlbumsWindow)

    tab = PublisherDetailTab(controller)
    try:
        tab.load_publisher_data(1)
        assert tab.tracks_label.text() == "1 album"

        tab._open_albums_window()

        # Simulate an album being newly associated with this publisher
        # while the (still-open) albums popup edits it.
        album_ids.append(2)

        received = []
        tab.albums_changed.connect(lambda: received.append(True))
        tab._albums_window.albums_changed.emit()

        assert tab.tracks_label.text() == "2 albums"
        assert tab.associations_btn.text() == "View Albums (2)"
        assert received == [True]
    finally:
        tab.deleteLater()


def test_format_years_folds_status_into_year_range():
    active_with_start = SimpleNamespace(begin_year=1996, end_year=None, is_active=1)
    assert PublisherDetailTab._format_years(active_with_start) == "1996–Current"  # noqa: RUF001

    inactive_with_end = SimpleNamespace(begin_year=1996, end_year=2008, is_active=0)
    assert PublisherDetailTab._format_years(inactive_with_end) == "1996–2008"  # noqa: RUF001

    inactive_open_ended = SimpleNamespace(begin_year=1996, end_year=None, is_active=0)
    assert PublisherDetailTab._format_years(inactive_open_ended) == "1996–"  # noqa: RUF001

    no_years = SimpleNamespace(begin_year=None, end_year=None, is_active=1)
    assert PublisherDetailTab._format_years(no_years) == "Active"


def test_places_card_hidden_when_publisher_has_no_places(monkeypatch, qapp):
    publisher = _make_publisher()
    controller = _StubController(publisher, [])
    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: [])

    tab = PublisherDetailTab(controller)
    try:
        tab.load_publisher_data(1)
        assert tab.places_card.isHidden()
    finally:
        tab.deleteLater()


def test_places_card_shows_association_type_label(monkeypatch, qapp):
    publisher = _make_publisher()
    place = SimpleNamespace(place_id=1, place_name="Nashville, TN")
    assoc_type = SimpleNamespace(type_name="Headquartered In")
    place_assoc = SimpleNamespace(place_id=1, association_type=assoc_type)

    class _GetWithPlaces(_StubGet):
        def get_all_entities(self, model_name, **kwargs):
            if model_name == "PlaceAssociation":
                return [place_assoc]
            return []

        def get_entity_object(self, model_name, **kwargs):
            if model_name == "Place":
                return place
            return self._publisher

    controller = _StubController(publisher, [])
    controller.get = _GetWithPlaces(publisher, [])
    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: [])

    tab = PublisherDetailTab(controller)
    try:
        tab.load_publisher_data(1)
        assert not tab.places_card.isHidden()
        labels = [tab.places_layout.itemAt(i).widget().text() for i in range(tab.places_layout.count())]
        assert labels == ["Headquartered In: Nashville, TN"]
    finally:
        tab.deleteLater()
