"""
Regression tests for AlbumTabBuilder.

Motivating bugs:
- The Add Artist Credit button required 2+ characters in both search
  boxes, but pressing Enter in either box called _handle_add() directly,
  bypassing that minimum.
- _build_publishers_section / _build_places_section made unguarded
  controller calls, unlike the Awards section's guarded equivalent, so a
  bad row would raise uncaught during tab construction.
"""

from PySide6.QtWidgets import QMessageBox
import pytest

from src.album.album_tab import AlbumTabBuilder
from src.common.entity_completer_edit import invalidate_entity_cache


class _FakeGet:
    def __init__(self, raise_on_publishers=False, raise_on_places=False):
        self._raise_on_publishers = raise_on_publishers
        self._raise_on_places = raise_on_places

    def count_entities(self, model_name):
        return 0

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "AlbumPublisher" and self._raise_on_publishers:
            raise AttributeError("boom")
        return []

    def get_entity_object(self, model_name, **kwargs):
        return None


class _FakeController:
    def __init__(self, **kw):
        self.get = _FakeGet(**kw)


class _FakeHelper:
    def __init__(self):
        self.add_calls = []

    def add_artist_credit(self, artist_names, role_names, **kwargs):
        self.add_calls.append((artist_names, role_names, kwargs))

    def add_publisher(self):
        pass

    def add_place(self):
        pass

    def remove_publisher(self, *a, **k):
        pass

    def remove_place(self, *a, **k):
        pass


class _StubAlbum:
    def __init__(self, album_id=1):
        self.album_id = album_id
        self.album_roles = []


class _Host:
    def __init__(self, controller, raise_on_places=False):
        self.controller = controller
        self.album = _StubAlbum()
        self.helper = _FakeHelper()
        self._raise_on_places = raise_on_places

    def get_album_place_associations(self):
        if self._raise_on_places:
            raise AttributeError("boom")
        return []


@pytest.fixture(autouse=True)
def _clear_entity_cache():
    invalidate_entity_cache()
    yield
    invalidate_entity_cache()


def test_enter_key_does_not_bypass_minimum_length_gate(qapp):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)

    row = builder._build_add_artist_credit_row()
    search_widgets = [w for w in row.findChildren(object) if hasattr(w, "returnPressed")]
    assert len(search_widgets) == 2
    artist_search, role_search = search_widgets

    artist_search.setText("A")  # 1 char -- below the 2-char minimum
    role_search.setText("B")
    role_search.returnPressed.emit()

    assert view.helper.add_calls == []


def test_enter_key_submits_once_minimum_length_is_met(qapp):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)

    row = builder._build_add_artist_credit_row()
    search_widgets = [w for w in row.findChildren(object) if hasattr(w, "returnPressed")]
    artist_search, role_search = search_widgets

    artist_search.setText("Beatles")
    role_search.setText("Performer")
    role_search.returnPressed.emit()

    assert len(view.helper.add_calls) == 1


def test_publishers_section_bad_row_does_not_raise(qapp):
    controller = _FakeController(raise_on_publishers=True)
    view = _Host(controller)
    builder = AlbumTabBuilder(view)

    group = builder._build_publishers_section()  # must not raise
    assert group is not None


def test_places_section_bad_row_does_not_raise(qapp):
    controller = _FakeController()
    view = _Host(controller, raise_on_places=True)
    builder = AlbumTabBuilder(view)

    group = builder._build_places_section()  # must not raise
    assert group is not None


def test_confirm_remove_declined_does_not_call_through(qapp, monkeypatch):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    called = []
    should_call = builder._confirm_remove("Test Item")
    if should_call:
        called.append(True)

    assert should_call is False
    assert called == []


def test_confirm_remove_accepted_returns_true(qapp, monkeypatch):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    assert builder._confirm_remove("Test Item") is True
