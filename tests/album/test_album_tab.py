"""
Regression tests for AlbumTabBuilder.

Motivating bugs:
- The Add Artist Credit button required 2+ characters in both search
  boxes, but pressing Enter in either box called _handle_add() directly,
  bypassing that minimum.
- _build_publishers_section / _build_places_section made unguarded
  controller calls, unlike the Awards section's guarded equivalent, so a
  bad row would raise uncaught during tab construction.
- Publisher/place/award/artist-credit "Remove" buttons popped a "Confirm
  Remove" QMessageBox before calling through, even though these rows are
  trivial to re-add -- Remove must call the helper directly.
"""

from PySide6.QtWidgets import QMessageBox, QPushButton
import pytest

from src.album.album_tab import AlbumTabBuilder
from src.common.widgets.entity_completer_edit import invalidate_entity_cache


class _FakePublisher:
    def __init__(self, publisher_id=1, publisher_name="Test Publisher", MBID=None):
        self.publisher_id = publisher_id
        self.publisher_name = publisher_name
        self.MBID = MBID


class _FakeAlbumPublisher:
    def __init__(self, publisher_id=1):
        self.publisher_id = publisher_id


class _FakePlace:
    def __init__(self, place_id=1, place_name="Test Place", MBID=None):
        self.place_id = place_id
        self.place_name = place_name
        self.MBID = MBID


class _FakePlaceAssociation:
    def __init__(self, place_id=1):
        self.place_id = place_id
        self.association_type = None


class _FakeAward:
    award_name = "Test Award"
    award_year = None
    award_category = None
    award_description = None


class _FakeRoleAssoc:
    artist = None


class _FakeGet:
    def __init__(self, raise_on_publishers=False, raise_on_places=False, with_publisher=False, place=None):
        self._raise_on_publishers = raise_on_publishers
        self._raise_on_places = raise_on_places
        self._with_publisher = with_publisher
        self._place = place

    def count_entities(self, model_name):
        return 0

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "AlbumPublisher":
            if self._raise_on_publishers:
                raise AttributeError("boom")
            if self._with_publisher:
                return [_FakeAlbumPublisher()]
        return []

    def get_entity_object(self, model_name, **kwargs):
        if model_name == "Publisher" and self._with_publisher:
            return _FakePublisher()
        if model_name == "Place" and self._place is not None:
            return self._place
        return None


class _FakeController:
    def __init__(self, **kw):
        self.get = _FakeGet(**kw)


class _FakeHelper:
    def __init__(self):
        self.add_calls = []
        self.remove_publisher_calls = []
        self.remove_place_calls = []
        self.remove_award_calls = []
        self.remove_artist_credit_calls = []

    def add_artist_credit(self, artist_names, role_names, **kwargs):
        self.add_calls.append((artist_names, role_names, kwargs))

    def add_publisher(self):
        pass

    def add_place(self):
        pass

    def remove_publisher(self, *a, **k):
        self.remove_publisher_calls.append((a, k))

    def remove_place(self, *a, **k):
        self.remove_place_calls.append((a, k))

    def remove_album_award_association(self, *a, **k):
        self.remove_award_calls.append((a, k))

    def remove_artist_credit(self, *a, **k):
        self.remove_artist_credit_calls.append((a, k))


class _StubAlbum:
    def __init__(self, album_id=1):
        self.album_id = album_id
        self.album_roles = []


class _Host:
    def __init__(self, controller, raise_on_places=False, place_associations=None):
        self.controller = controller
        self.album = _StubAlbum()
        self.helper = _FakeHelper()
        self._raise_on_places = raise_on_places
        self._place_associations = place_associations or []

    def get_album_place_associations(self):
        if self._raise_on_places:
            raise AttributeError("boom")
        return self._place_associations


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


def _forbid_confirmation_dialog(monkeypatch):
    """Fail the test if any code path still pops a confirmation QMessageBox."""

    def _boom(*a, **k):
        raise AssertionError("Remove must not show a confirmation dialog")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_boom))


def test_publisher_remove_button_calls_through_without_confirmation(qapp, monkeypatch):
    controller = _FakeController(with_publisher=True)
    view = _Host(controller)
    builder = AlbumTabBuilder(view)
    _forbid_confirmation_dialog(monkeypatch)

    group = builder._build_publishers_section()
    remove_btn = next(w for w in group.findChildren(QPushButton) if w.text() == "Remove")
    remove_btn.click()

    assert len(view.helper.remove_publisher_calls) == 1


def test_place_remove_button_calls_through_without_confirmation(qapp, monkeypatch):
    controller = _FakeController(place=_FakePlace())
    view = _Host(controller, place_associations=[_FakePlaceAssociation()])
    builder = AlbumTabBuilder(view)
    _forbid_confirmation_dialog(monkeypatch)

    group = builder._build_places_section()
    remove_btn = next(w for w in group.findChildren(QPushButton) if w.text() == "Remove")
    remove_btn.click()

    assert len(view.helper.remove_place_calls) == 1


def test_award_remove_button_calls_through_without_confirmation(qapp, monkeypatch):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)
    _forbid_confirmation_dialog(monkeypatch)

    widget = builder._build_award_widget(_FakeAward())
    remove_btn = next(w for w in widget.findChildren(QPushButton) if w.text() == "Remove Award")
    remove_btn.click()

    assert len(view.helper.remove_award_calls) == 1


def test_artist_credit_remove_button_calls_through_without_confirmation(qapp, monkeypatch):
    controller = _FakeController()
    view = _Host(controller)
    builder = AlbumTabBuilder(view)
    _forbid_confirmation_dialog(monkeypatch)

    role_assoc = _FakeRoleAssoc()
    chip = builder._build_artist_credit_chip("Test Artist", role_assoc, 0, 0)
    remove_btn = next(w for w in chip.findChildren(QPushButton) if w.text() == "Remove")
    remove_btn.click()

    assert len(view.helper.remove_artist_credit_calls) == 1
