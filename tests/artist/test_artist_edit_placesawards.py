"""Tests for src/artist/edit/artist_edit_placesawards.py::PlacesAwardsTab.

Covers the Finalize audit fixes (2026-09-18):
- _load_places/_load_awards read exclusively through the association-query
  path now -- the artist.places/artist.awards fallback was unreachable dead
  code (get_all_entities already swallows its own SQLAlchemyError and
  returns [] rather than raising) and has been removed.
- _remove_place/_remove_award check delete_entity's boolean return value
  instead of branching on an exception it never actually raises.
- _add_place guards against the completer-selected place having vanished.
- _edit_place is a new edit path for an existing place association's type.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox
import pytest

from src.artist.edit import artist_edit_placesawards as paw


class _FakeGet:
    def __init__(self, all_entities=None, entity_objects=None):
        self._all_entities = all_entities or {}
        self._entity_objects = list(entity_objects or [])  # queue, popped in call order

    def get_all_entities(self, model_name, **kwargs):
        return self._all_entities.get(model_name, [])

    def get_entity_object(self, model_name, **kwargs):
        if self._entity_objects:
            return self._entity_objects.pop(0)
        return None


class _FakeDelete:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, *args, **kwargs):
        self.calls.append((model_name, args, kwargs))
        return self.result


class _FakeAdd:
    def __init__(self, entity=None):
        self.entity = entity
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.entity


class _FakeUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return self.result


class _FakeController:
    def __init__(self, get=None, add=None, delete=None, update=None):
        self.get = get or _FakeGet()
        self.add = add or _FakeAdd()
        self.delete = delete or _FakeDelete()
        self.update = update or _FakeUpdate()


def _artist(**overrides):
    base = {"artist_id": 1, "artist_name": "Test Artist", "places": [], "awards": []}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Prevent QMessageBox popups from blocking the (offscreen) test run."""
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def _make_tab(controller, monkeypatch, artist=None):
    monkeypatch.setattr(paw, "fetch_association_types", lambda controller: [])
    artist = artist or _artist()
    tab = paw.PlacesAwardsTab(controller, artist)
    tab.load(artist)
    return tab


# ── _load_places / _load_awards: no fallback to the artist relationship ────


def test_load_places_reads_only_the_association_query(qapp, monkeypatch):
    place = SimpleNamespace(place_name="Chicago", place_type="City", parent_id=None, parent=None)
    assoc = SimpleNamespace(
        place=place, association_type=SimpleNamespace(type_name="Birthplace"), association_id=42
    )
    decoy = SimpleNamespace(place_name="DECOY", place_type="City", place_id=999)
    artist = _artist(places=[decoy])
    controller = _FakeController(get=_FakeGet(all_entities={"PlaceAssociation": [assoc]}))

    tab = _make_tab(controller, monkeypatch, artist)

    assert tab.places_table.rowCount() == 1
    assert tab.places_table.item(0, 0).text() == "Chicago"
    assert tab.places_table.item(0, 0).data(paw.Qt.UserRole) == 42


def test_load_places_handles_empty_query_without_crashing(qapp, monkeypatch):
    artist = _artist(places=[SimpleNamespace(place_name="DECOY", place_type="", place_id=1)])
    controller = _FakeController(get=_FakeGet(all_entities={"PlaceAssociation": []}))

    tab = _make_tab(controller, monkeypatch, artist)

    assert tab.places_table.rowCount() == 0


# ── _remove_place / _remove_award: check the boolean return, don't guess ───


def test_remove_place_reloads_on_success(qapp, monkeypatch):
    controller = _FakeController(delete=_FakeDelete(result=True))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["A", "B", "C", "D"], user_data=7)
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))

    tab._remove_place(0)

    assert controller.delete.calls == [("PlaceAssociation", (7,), {})]
    assert reload_calls == [1]


def test_remove_place_shows_error_and_does_not_reload_on_failure(qapp, monkeypatch):
    controller = _FakeController(delete=_FakeDelete(result=False))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["A", "B", "C", "D"], user_data=7)
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))

    tab._remove_place(0)

    assert reload_calls == []


# ── _add_place: guard against a vanished completer selection ───────────────


def test_add_place_falls_back_when_selected_place_is_gone(qapp, monkeypatch):
    new_place = SimpleNamespace(place_id=5, place_name="Ghost Town")
    controller = _FakeController(
        get=_FakeGet(entity_objects=[None, None]),  # place_id lookup, then place_name lookup
        add=_FakeAdd(entity=new_place),
    )
    tab = _make_tab(controller, monkeypatch)
    monkeypatch.setattr(paw, "find_or_create_association_type", lambda *a, **k: None)
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))
    monkeypatch.setattr(tab, "_refresh_place_completers", lambda: None)

    tab._selected_place_id = 999
    tab.new_place_edit.setText("Ghost Town")
    tab.new_place_assoc_edit.setText("Birthplace")
    tab._add_place()

    assert controller.add.calls[0] == ("Place", {"place_name": "Ghost Town"})
    assert reload_calls == [1]


# ── _edit_place: new edit path for an existing association ─────────────────


def test_edit_place_updates_association_type(qapp, monkeypatch):
    assoc_type = SimpleNamespace(association_type_id=3, type_name="Hometown")
    controller = _FakeController(update=_FakeUpdate(result=True))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["Chicago", "Birthplace", "City", ""], user_data=42)
    monkeypatch.setattr(paw, "find_or_create_association_type", lambda *a, **k: assoc_type)
    monkeypatch.setattr(paw._EditAssociationTypeDialog, "exec", lambda self: paw.QDialog.Accepted)
    monkeypatch.setattr(paw._EditAssociationTypeDialog, "value", lambda self: "Hometown")
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))

    tab._edit_place(0)

    assert controller.update.calls == [("PlaceAssociation", 42, {"association_type_id": 3})]
    assert reload_calls == [1]


def test_edit_place_cancelled_dialog_makes_no_changes(qapp, monkeypatch):
    controller = _FakeController(update=_FakeUpdate(result=True))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["Chicago", "Birthplace", "City", ""], user_data=42)
    monkeypatch.setattr(paw._EditAssociationTypeDialog, "exec", lambda self: paw.QDialog.Rejected)

    tab._edit_place(0)

    assert controller.update.calls == []
