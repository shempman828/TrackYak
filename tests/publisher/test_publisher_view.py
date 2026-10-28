"""PublisherView: the view, the detail panel, and the albums list."""

from types import SimpleNamespace
from typing import ClassVar

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher
import src.publisher.publisher_albums as publisher_albums_module
from src.publisher.publisher_albums import PublisherAlbumsWindow
import src.publisher.publisher_detail as publisher_detail_module
from src.publisher.publisher_detail import PublisherDetailTab
import src.publisher.publisher_view as publisher_view_module
from src.publisher.publisher_view import PublisherView, plan_child_reparenting

# ---------------------------------------------------------------------------
# Tests for PublisherView: delete reparenting and the duplicate-scan cancel path.
# ---------------------------------------------------------------------------


def _pub(publisher_id, parent_id=None):
    return SimpleNamespace(publisher_id=publisher_id, parent_id=parent_id)


def test_children_of_deleted_publisher_move_to_its_parent():
    publishers = [_pub(1), _pub(2, 1), _pub(3, 2), _pub(4, 2)]
    assert plan_child_reparenting(publishers, {2}) == {3: 1, 4: 1}


def test_children_skip_ancestors_that_are_also_deleted():
    publishers = [_pub(1), _pub(2, 1), _pub(3, 2), _pub(4, 3)]
    assert plan_child_reparenting(publishers, {2, 3}) == {4: 1}


def test_children_become_roots_when_whole_chain_is_deleted():
    publishers = [_pub(1), _pub(2, 1)]
    assert plan_child_reparenting(publishers, {1}) == {2: None}


def test_deleted_cycle_does_not_loop():
    publishers = [_pub(1, 2), _pub(2, 1), _pub(3, 1)]
    assert plan_child_reparenting(publishers, {1, 2}) == {3: None}


class _StubScanWorker(QObject):
    """Stands in for PublisherFuzzyScanWorker; start() finishes after a cancel."""

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, entries, threshold):
        super().__init__()
        self.entries = entries
        self.is_cancelled = False

    def request_cancel(self):
        self.is_cancelled = True

    def isRunning(self):
        return False

    def start(self):
        self.request_cancel()
        self.finished.emit([(self.entries[0][0], self.entries[1][0], 95)])


@pytest.fixture
def view(monkeypatch, qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Publisher(publisher_name="Sony"), Publisher(publisher_name="Sonny")])
    session.commit()
    view = PublisherView(SimpleNamespace(get=GetFromDB(session)))
    yield view
    view.deleteLater()
    session.close()


def test_cancelled_scan_does_not_open_review_dialog(view, monkeypatch):
    opened = []
    monkeypatch.setattr(publisher_view_module, "PublisherFuzzyScanWorker", _StubScanWorker)
    monkeypatch.setattr(publisher_view_module, "PublisherFuzzyMatchDialog", lambda *a, **k: opened.append(a))

    view.find_fuzzy_matches()

    assert opened == []


def test_search_filter_is_debounced(view):
    view.search_bar.setText("Sonn")
    assert view.count_label.text() == "2 publishers"
    view._search_timer.timeout.emit()
    assert view.count_label.text() == "1 of 2 publishers"


# ---------------------------------------------------------------------------
# Regression test: editing an album from the publisher view's "View Albums"
# ---------------------------------------------------------------------------


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
        assert tab.albums_label.text() == "1 album"

        tab._open_albums_window()

        # Simulate an album being newly associated with this publisher
        # while the (still-open) albums popup edits it.
        album_ids.append(2)

        received = []
        tab.albums_changed.connect(lambda: received.append(True))
        tab._albums_window.albums_changed.emit()

        assert tab.albums_label.text() == "2 albums"
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
    place_assoc = SimpleNamespace(place_id=1, place=place, association_type=assoc_type)

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


def test_publisher_name_is_html_escaped_for_every_review_tier(monkeypatch, qapp):
    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: [])
    for first_pass, second_pass in ((False, False), (True, False), (True, True)):
        publisher = _make_publisher()
        publisher.publisher_name = "<b>Bold</b> & Co"
        publisher.first_pass, publisher.second_pass = first_pass, second_pass
        tab = PublisherDetailTab(_StubController(publisher, []))
        try:
            tab.load_publisher_data(1)
            assert tab.name_label.text().startswith("&lt;b&gt;Bold&lt;/b&gt; &amp; Co")
        finally:
            tab.deleteLater()


def test_null_publisher_name_does_not_raise(monkeypatch, qapp):
    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: [])
    publisher = _make_publisher()
    publisher.publisher_name = None
    tab = PublisherDetailTab(_StubController(publisher, []))
    try:
        tab.load_publisher_data(1)
        assert tab.name_label.text() == ""
    finally:
        tab.deleteLater()


def test_view_albums_reuses_open_window(monkeypatch, qapp):
    publisher = _make_publisher()
    monkeypatch.setattr(publisher_detail_module, "get_publisher_albums", lambda *a, **k: [])
    monkeypatch.setattr(publisher_detail_module, "PublisherAlbumsWindow", _StubAlbumsWindow)
    tab = PublisherDetailTab(_StubController(publisher, []))
    try:
        tab.load_publisher_data(1)
        tab._open_albums_window()
        first = tab._albums_window
        first.show()
        tab._open_albums_window()
        assert tab._albums_window is first
        first.close()
    finally:
        tab.deleteLater()


# ---------------------------------------------------------------------------
# Regression test for the publisher album view: double-clicking an album
# ---------------------------------------------------------------------------


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


class _StubGetAlbums:
    def __init__(self, album):
        self._album = album
        self.calls = []

    def get_entity_object(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self._album


class _StubControllerAlbums:
    def __init__(self, album):
        self.get = _StubGetAlbums(album)


def _make_publisher_albums():
    return SimpleNamespace(publisher_id=1, publisher_name="Test Publisher")


def test_double_click_opens_album_editor(monkeypatch, qapp):
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubControllerAlbums(album)

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", lambda *a, **k: [])

    window = PublisherAlbumsWindow(controller, _make_publisher_albums())
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
    controller = _StubControllerAlbums(album)

    load_calls = []

    def _fake_get_publisher_albums(*a, **k):
        load_calls.append(True)
        return []

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", _fake_get_publisher_albums)

    window = PublisherAlbumsWindow(controller, _make_publisher_albums())
    try:
        assert len(load_calls) == 1  # initial load on construction

        window._open_album_editor(album)

        assert len(load_calls) == 2  # reloaded after editor closed
    finally:
        window.deleteLater()


def test_album_edit_emits_albums_changed(monkeypatch, qapp):
    """Editing an album must emit albums_changed so the owning publisher
    detail panel and tree (whose album counts are otherwise left stale)
    know to refresh."""
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubControllerAlbums(album)

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", lambda *a, **k: [])

    window = PublisherAlbumsWindow(controller, _make_publisher_albums())
    try:
        received = []
        window.albums_changed.connect(lambda: received.append(True))

        window._open_album_editor(album)

        assert received == [True]
    finally:
        window.deleteLater()


def test_flow_double_click_signal_is_connected_to_editor(monkeypatch, qapp):
    _StubAlbumEditor.instances = []
    album = SimpleNamespace(album_id=7, album_name="Some Album")
    controller = _StubControllerAlbums(album)

    monkeypatch.setattr(publisher_albums_module, "AlbumEditor", _StubAlbumEditor)
    monkeypatch.setattr(publisher_albums_module, "get_publisher_albums", lambda *a, **k: [])

    window = PublisherAlbumsWindow(controller, _make_publisher_albums())
    try:
        window.flow.albumDoubleClicked.emit(album)

        assert len(_StubAlbumEditor.instances) == 1
        assert _StubAlbumEditor.instances[0].album is album
    finally:
        window.deleteLater()
