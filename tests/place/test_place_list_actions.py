"""Tests for ListView place actions: failed writes, delete reparenting, and new parent/child places."""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from src.place import place_list
from src.place.dialogs.place_edit import PlaceEditDialog
from src.place.place_list import ListView


class _FakePlace:
    def __init__(self, place_id, place_name, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.MBID = None
        self.parent_id = parent_id
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGet:
    def __init__(self, places):
        self.places = places

    def get_all_entities(self, model_name, **_filters):
        return list(self.places)

    def get_entity_object(self, model_name, **filters):
        return next((p for p in self.places if p.place_id == filters.get("place_id")), None)


class _Recorder:
    """Records calls to any method and returns a preset result."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.result

        return method


def _make(places, add_result=None, update_result=True, delete_result=True):
    controller = SimpleNamespace(get=_StubGet(places), add=_Recorder(add_result), update=_Recorder(update_result), delete=_Recorder(delete_result))
    view = ListView(controller)
    view.load_places()
    return view, controller


def _select(view, *place_ids):
    view.tree_widget.clearSelection()
    for place_id in place_ids:
        view._find_item(place_id).setSelected(True)


def _errors(monkeypatch):
    shown = []
    monkeypatch.setattr(place_list.QMessageBox, "critical", lambda *args: shown.append(args[2]))
    return shown


def _places():
    # 1 Country > 2 State > 3 City > 4 Venue
    return [_FakePlace(1, "Country"), _FakePlace(2, "State", 1), _FakePlace(3, "City", 2), _FakePlace(4, "Venue", 3)]


def test_delete_moves_children_to_the_deleted_place_parent(qapp, monkeypatch):
    view, controller = _make(_places())
    monkeypatch.setattr(place_list.QMessageBox, "question", lambda *args: QMessageBox.Yes)
    _select(view, 2)
    try:
        view.delete_selected_places()

        assert ("update_entities", ("Place", [3]), {"parent_id": 1}) in controller.update.calls
        assert ("delete_entity", ("Place", 2), {}) in controller.delete.calls
    finally:
        view.close()


def test_delete_of_parent_and_child_moves_grandchildren_past_both(qapp, monkeypatch):
    view, controller = _make(_places())
    monkeypatch.setattr(place_list.QMessageBox, "question", lambda *args: QMessageBox.Yes)
    _select(view, 2, 3)
    try:
        view.delete_selected_places()

        assert ("update_entities", ("Place", [4]), {"parent_id": 1}) in controller.update.calls
        assert len(controller.delete.calls) == 2
    finally:
        view.close()


def test_failed_delete_is_reported(qapp, monkeypatch):
    view, _controller = _make(_places(), delete_result=False)
    monkeypatch.setattr(place_list.QMessageBox, "question", lambda *args: QMessageBox.Yes)
    shown = _errors(monkeypatch)
    _select(view, 4)
    try:
        view.delete_selected_places()

        assert shown and "Venue" in shown[0]
    finally:
        view.close()


def test_failed_add_is_reported(qapp, monkeypatch):
    view, _controller = _make(_places(), add_result=None)
    shown = _errors(monkeypatch)

    def fake_exec(self):
        self.name_edit.setText("New")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    try:
        view.add_place()

        assert shown == ["Could not create the place."]
    finally:
        view.close()


def test_failed_edit_is_reported(qapp, monkeypatch):
    view, _controller = _make(_places(), update_result=False)
    shown = _errors(monkeypatch)
    monkeypatch.setattr(PlaceEditDialog, "exec_", lambda self: PlaceEditDialog.Accepted)
    try:
        view.edit_place_for(view.places_by_id[4])

        assert len(shown) == 1
    finally:
        view.close()


def test_new_child_place_is_created_in_one_write_under_the_place(qapp, monkeypatch):
    view, controller = _make(_places(), add_result=_FakePlace(9, "Club"))

    def fake_exec(self):
        self.name_edit.setText("Club")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    try:
        view.create_new_child_place(view.places_by_id[3])

        assert len(controller.add.calls) == 1
        assert controller.add.calls[0][2]["parent_id"] == 3
        assert controller.update.calls == []
    finally:
        view.close()


def test_new_parent_place_is_removed_again_when_the_link_fails(qapp, monkeypatch):
    view, controller = _make(_places(), add_result=_FakePlace(9, "District"), update_result=False)
    shown = _errors(monkeypatch)

    def fake_exec(self):
        self.name_edit.setText("District")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    try:
        view.create_new_parent_place(view.places_by_id[4])

        assert controller.add.calls[0][2]["parent_id"] == 3  # takes over Venue's old parent slot
        assert ("delete_entity", ("Place", 9), {}) in controller.delete.calls
        assert len(shown) == 1
    finally:
        view.close()


def test_most_used_sort_uses_subtree_counts(qapp):
    places = [_FakePlace(1, "A"), _FakePlace(2, "B"), _FakePlace(3, "B child", 2)]
    places[2].associations = [object()] * 3
    places[0].associations = [object()]
    view, _controller = _make(places)
    try:
        view.sort_control.setCurrentIndex(view._SORT_ASSOC)

        top = [view.tree_widget.topLevelItem(i).text(0) for i in range(view.tree_widget.topLevelItemCount())]
        assert top == ["B", "A"]
    finally:
        view.close()


def test_place_that_is_its_own_parent_still_shows_in_the_tree(qapp):
    places = [_FakePlace(1, "Country"), _FakePlace(2, "Loop", 2), _FakePlace(3, "Loop child", 2)]
    view, _controller = _make(places)
    try:
        assert view.tree_widget.count_items()[0] == 3
        assert view._find_item(3).parent() is view._find_item(2)
    finally:
        view.close()
