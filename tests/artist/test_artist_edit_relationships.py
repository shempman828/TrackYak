"""Artist editor relationship edits: alias swap, influences, group
membership, and places/awards. Each edit must be atomic and must keep the
row when the write fails.
"""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
import pytest

from src.artist.edit import artist_edit_influences, artist_edit_member, artist_edit_placesawards as paw
from src.artist.edit.artist_edit_alias import AliasesTab
from src.artist.edit.artist_edit_influences import DIR_INFLUENCED, InfluencesTab
from src.artist.edit.artist_edit_member import _MembershipPanelBase

# ---------------------------------------------------------------------------
# Tests for src/artist/edit/artist_edit_alias.py::AliasesTab._perform_swap.
# ---------------------------------------------------------------------------


class _FakeAdd:
    def __init__(self, entity=None):
        self.entity = entity
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.entity


class _FakeDelete:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, entity_id):
        self.calls.append((model_name, entity_id))
        return self.result


class _FakeUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return self.result


class _FakeController:
    def __init__(self, add=None, delete=None, update=None):
        self.add = add or _FakeAdd()
        self.delete = delete or _FakeDelete()
        self.update = update or _FakeUpdate()


class _FakeAliasTab:
    """Bare stand-in for AliasesTab's instance state -- _perform_swap only
    touches self.controller and self.artist, so the full Qt widget tree
    (built by EntityAliasesTab.__init__) isn't needed for this logic."""

    def __init__(self, controller, artist):
        self.controller = controller
        self.artist = artist


def _artist(artist_id=1, artist_name="Old Name"):
    return SimpleNamespace(artist_id=artist_id, artist_name=artist_name)


@pytest.fixture(autouse=True)
def _confirm_yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def test_swap_adds_alias_then_renames_then_deletes_old():
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is True
    assert artist.artist_name == "New Name"
    assert controller.add.calls == [
        ("ArtistAlias", {"artist_id": 1, "alias_name": "Old Name", "alias_type": "Former Name"})
    ]
    assert controller.delete.calls == [("ArtistAlias", 10)]


def test_swap_aborts_without_losing_old_name_when_alias_add_fails():
    artist = _artist()
    controller = _FakeController(add=_FakeAdd(entity=None))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is False
    assert artist.artist_name == "Old Name"  # never renamed
    assert controller.delete.calls == []  # old alias row never touched


def test_swap_rolls_back_new_alias_when_rename_fails():
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias), update=_FakeUpdate(result=False))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is False
    assert artist.artist_name == "Old Name"
    assert controller.delete.calls == [("ArtistAlias", 99)]  # rolled back the new alias only


def test_swap_tolerates_failure_to_remove_the_promoted_alias():
    """The promoted alias row (alias_id) is deleted last; if that fails the
    worst case is a harmless leftover alias duplicating the new primary
    name, not a lost old name -- so the swap still reports success."""
    artist = _artist()
    new_alias = SimpleNamespace(alias_id=99)
    controller = _FakeController(add=_FakeAdd(entity=new_alias), delete=_FakeDelete(result=False))
    tab = _FakeAliasTab(controller, artist)

    result = AliasesTab._perform_swap(tab, alias_id=10, new_primary="New Name", alias_type="")

    assert result is True
    assert artist.artist_name == "New Name"


# ---------------------------------------------------------------------------
# Tests for src/artist/edit/artist_edit_influences.py::InfluencesTab's swap
# ---------------------------------------------------------------------------


class _FakeUpdateInfluences:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity_by_filter(self, model_name, filters, **kwargs):
        self.calls.append((model_name, filters, kwargs))
        return self.result


class _FakeControllerInfluences:
    def __init__(self, update=None):
        self.update = update or _FakeUpdateInfluences()


class _FakeItem:
    def __init__(self, text="", data=None):
        self._text = text
        self._data = data or {}

    def text(self):
        return self._text

    def data(self, role):
        return self._data.get(role)


class _FakeTable:
    def __init__(self, items):
        self._items = items  # {(row, col): _FakeItem}

    def item(self, row, col):
        return self._items.get((row, col))


class _FakeInfluencesSelf:
    """Bare stand-in exposing only what the two actions touch on self --
    skips constructing the real table-backed widget, which isn't relevant
    to the DB-call logic being fixed."""

    _row_key = InfluencesTab._row_key

    def __init__(self, controller, rows=None, selected=None, table=None):
        self.controller = controller
        self.artist = SimpleNamespace(artist_id=1)
        self._rows = rows or []
        self._selected = selected or []
        self.table = table
        self.reload_calls = 0

    def _selected_row_data(self):
        return self._selected

    def _reload_and_refresh(self):
        self.reload_calls += 1


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def test_swap_selected_uses_a_single_atomic_update():
    row = {"direction": DIR_INFLUENCED, "other_id": 2, "name": "Other", "description": "d"}
    update = _FakeUpdateInfluences(result=True)
    fake = _FakeInfluencesSelf(_FakeControllerInfluences(update), rows=[row], selected=[row])

    InfluencesTab._handle_swap_selected(fake)

    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"influencer_id": 2, "influenced_id": 1},
        )
    ]
    assert fake.reload_calls == 1


def test_swap_selected_never_deletes_the_row_it_cannot_replace():
    row = {"direction": DIR_INFLUENCED, "other_id": 2, "name": "Other", "description": "d"}
    update = _FakeUpdateInfluences(result=False)
    fake = _FakeInfluencesSelf(_FakeControllerInfluences(update), rows=[row], selected=[row])

    InfluencesTab._handle_swap_selected(fake)

    # Only the single update was attempted -- no separate delete call exists
    # in this code path any more, so a failed swap can't lose the row.
    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"influencer_id": 2, "influenced_id": 1},
        )
    ]
    assert fake.reload_calls == 1


class _FakeDescDialog:
    """Stand-in for _EditDescriptionDialog that skips real QDialog
    construction (which requires a genuine QWidget parent)."""

    return_value = "New description"

    def __init__(self, description, parent=None):
        self.description = description

    def exec(self):
        return QDialog.Accepted

    def value(self):
        return type(self).return_value


def test_edit_description_uses_update_entity_by_filter(monkeypatch):
    monkeypatch.setattr(artist_edit_influences, "_EditDescriptionDialog", _FakeDescDialog)
    _FakeDescDialog.return_value = "New description"

    rel_item = _FakeItem(data={Qt.UserRole: 2, Qt.UserRole + 1: DIR_INFLUENCED})
    desc_item = _FakeItem(text="Old description")
    table = _FakeTable({(0, 1): rel_item, (0, 2): desc_item})
    update = _FakeUpdateInfluences(result=True)
    fake = _FakeInfluencesSelf(_FakeControllerInfluences(update), table=table)

    InfluencesTab._handle_edit_description(fake, 0)

    assert update.calls == [
        (
            "ArtistInfluence",
            {"influencer_id": 1, "influenced_id": 2},
            {"description": "New description"},
        )
    ]
    assert fake.reload_calls == 1


def test_edit_description_failure_reports_error_without_losing_the_row(monkeypatch):
    monkeypatch.setattr(artist_edit_influences, "_EditDescriptionDialog", _FakeDescDialog)
    _FakeDescDialog.return_value = "New description"
    critical_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical_calls.append(a))

    rel_item = _FakeItem(data={Qt.UserRole: 2, Qt.UserRole + 1: DIR_INFLUENCED})
    desc_item = _FakeItem(text="Old description")
    table = _FakeTable({(0, 1): rel_item, (0, 2): desc_item})
    update = _FakeUpdateInfluences(result=False)
    fake = _FakeInfluencesSelf(_FakeControllerInfluences(update), table=table)

    InfluencesTab._handle_edit_description(fake, 0)

    # The update was attempted and reported as failed; unlike the old
    # delete-then-add pattern, nothing was deleted along the way.
    assert update.calls
    assert critical_calls
    assert fake.reload_calls == 0


# ---------------------------------------------------------------------------
# Tests for src/artist/edit/artist_edit_member.py::_MembershipPanelBase._edit.
# ---------------------------------------------------------------------------


class _FakeUpdateMember:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity_by_filter(self, model_name, filters, **kwargs):
        self.calls.append((model_name, filters, kwargs))
        return self.result


class _FakeControllerMember:
    def __init__(self, update=None):
        self.update = update or _FakeUpdateMember()


class _FakeItemMember:
    def __init__(self, text="", data=None):
        self._text = text
        self._data = data

    def text(self):
        return self._text

    def data(self, _role):
        return self._data


class _FakeTableMember:
    def __init__(self, items):
        self._items = items  # {(row, col): _FakeItemMember}

    def item(self, row, col):
        return self._items.get((row, col))


class _FakeMembershipSelf:
    """Bare stand-in exposing only what _edit touches on self -- skips
    constructing the real table-backed widget."""

    def __init__(self, controller, table):
        self.controller = controller
        self.table = table
        self.reload_calls = 0

    def _reload(self):
        self.reload_calls += 1


class _FakeEditDialog:
    """Stand-in for _EditMembershipDialog that skips real QDialog
    construction (which requires a genuine QWidget parent)."""

    def __init__(self, role, start_year, end_year, is_current, parent=None):
        self._values = {
            "role": role or None,
            "active_start_year": int(start_year) if start_year else None,
            "active_end_year": int(end_year) if end_year else None,
            "is_current": 1 if is_current else 0,
        }

    def exec(self):
        return QDialog.Accepted

    def values(self):
        return self._values


def _table_for_row(role="Guitarist", start="1990", end="", current="No"):
    return _FakeTableMember(
        {
            (0, 0): _FakeItemMember(data=(10, 20)),
            (0, 1): _FakeItemMember(text=role),
            (0, 2): _FakeItemMember(text=start),
            (0, 3): _FakeItemMember(text=end),
            (0, 4): _FakeItemMember(text=current),
        }
    )


@pytest.fixture(autouse=True)
def _fake_dialog(monkeypatch):
    monkeypatch.setattr(artist_edit_member, "_EditMembershipDialog", _FakeEditDialog)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def test_edit_membership_uses_a_single_atomic_update():
    update = _FakeUpdateMember(result=True)
    fake = _FakeMembershipSelf(_FakeControllerMember(update), _table_for_row())

    _MembershipPanelBase._edit(fake, 0)

    assert update.calls == [
        (
            "GroupMembership",
            {"group_id": 10, "member_id": 20},
            {
                "role": "Guitarist",
                "active_start_year": 1990,
                "active_end_year": None,
                "is_current": 0,
            },
        )
    ]
    assert fake.reload_calls == 1


def test_edit_membership_failure_reports_error_without_losing_the_row(monkeypatch):
    critical_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical_calls.append(a))

    update = _FakeUpdateMember(result=False)
    fake = _FakeMembershipSelf(_FakeControllerMember(update), _table_for_row())

    _MembershipPanelBase._edit(fake, 0)

    # The update was attempted and reported as failed; unlike the old
    # delete-then-add pattern, nothing was ever deleted.
    assert update.calls
    assert critical_calls
    assert fake.reload_calls == 0


def test_edit_membership_no_selection_data_is_a_no_op():
    table = _FakeTableMember({(0, 0): _FakeItemMember(data=None)})
    update = _FakeUpdateMember(result=True)
    fake = _FakeMembershipSelf(_FakeControllerMember(update), table)

    _MembershipPanelBase._edit(fake, 0)

    assert update.calls == []
    assert fake.reload_calls == 0


# ---------------------------------------------------------------------------
# Tests for src/artist/edit/artist_edit_placesawards.py::PlacesAwardsTab.
# ---------------------------------------------------------------------------


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


class _FakeDeletePlacesawards:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, *args, **kwargs):
        self.calls.append((model_name, args, kwargs))
        return self.result


class _FakeAddPlacesawards:
    def __init__(self, entity=None):
        self.entity = entity
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.entity


class _FakeUpdatePlacesawards:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return self.result


class _FakeControllerPlacesawards:
    def __init__(self, get=None, add=None, delete=None, update=None):
        self.get = get or _FakeGet()
        self.add = add or _FakeAddPlacesawards()
        self.delete = delete or _FakeDeletePlacesawards()
        self.update = update or _FakeUpdatePlacesawards()


def _artist_placesawards(**overrides):
    base = {"artist_id": 1, "artist_name": "Test Artist", "places": [], "awards": []}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_modal_dialogs_placesawards(monkeypatch):
    """Prevent QMessageBox popups from blocking the (offscreen) test run."""
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def _make_tab(controller, monkeypatch, artist=None):
    monkeypatch.setattr(paw, "fetch_association_types", lambda controller: [])
    artist = artist or _artist_placesawards()
    tab = paw.PlacesAwardsTab(controller, artist)
    tab.load(artist)
    return tab


def test_load_places_reads_only_the_association_query(qapp, monkeypatch):
    place = SimpleNamespace(place_name="Chicago", place_type="City", parent_id=None, parent=None)
    assoc = SimpleNamespace(
        place=place, association_type=SimpleNamespace(type_name="Birthplace"), association_id=42
    )
    decoy = SimpleNamespace(place_name="DECOY", place_type="City", place_id=999)
    artist = _artist_placesawards(places=[decoy])
    controller = _FakeControllerPlacesawards(get=_FakeGet(all_entities={"PlaceAssociation": [assoc]}))

    tab = _make_tab(controller, monkeypatch, artist)

    assert tab.places_table.rowCount() == 1
    assert tab.places_table.item(0, 0).text() == "Chicago"
    assert tab.places_table.item(0, 0).data(paw.Qt.UserRole) == 42


def test_load_places_handles_empty_query_without_crashing(qapp, monkeypatch):
    artist = _artist_placesawards(places=[SimpleNamespace(place_name="DECOY", place_type="", place_id=1)])
    controller = _FakeControllerPlacesawards(get=_FakeGet(all_entities={"PlaceAssociation": []}))

    tab = _make_tab(controller, monkeypatch, artist)

    assert tab.places_table.rowCount() == 0


def test_remove_place_reloads_on_success(qapp, monkeypatch):
    controller = _FakeControllerPlacesawards(delete=_FakeDeletePlacesawards(result=True))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["A", "B", "C", "D"], user_data=7)
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))

    tab._remove_place(0)

    assert controller.delete.calls == [("PlaceAssociation", (7,), {})]
    assert reload_calls == [1]


def test_remove_place_shows_error_and_does_not_reload_on_failure(qapp, monkeypatch):
    controller = _FakeControllerPlacesawards(delete=_FakeDeletePlacesawards(result=False))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["A", "B", "C", "D"], user_data=7)
    reload_calls = []
    monkeypatch.setattr(tab, "_reload_and_refresh", lambda: reload_calls.append(1))

    tab._remove_place(0)

    assert reload_calls == []


def test_add_place_falls_back_when_selected_place_is_gone(qapp, monkeypatch):
    new_place = SimpleNamespace(place_id=5, place_name="Ghost Town")
    controller = _FakeControllerPlacesawards(
        get=_FakeGet(entity_objects=[None, None]),  # place_id lookup, then place_name lookup
        add=_FakeAddPlacesawards(entity=new_place),
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


def test_edit_place_updates_association_type(qapp, monkeypatch):
    assoc_type = SimpleNamespace(association_type_id=3, type_name="Hometown")
    controller = _FakeControllerPlacesawards(update=_FakeUpdatePlacesawards(result=True))
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
    controller = _FakeControllerPlacesawards(update=_FakeUpdatePlacesawards(result=True))
    tab = _make_tab(controller, monkeypatch)
    paw._append_row(tab.places_table, ["Chicago", "Birthplace", "City", ""], user_data=42)
    monkeypatch.setattr(paw._EditAssociationTypeDialog, "exec", lambda self: paw.QDialog.Rejected)

    tab._edit_place(0)

    assert controller.update.calls == []
