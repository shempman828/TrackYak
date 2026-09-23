"""Tests for bulk-editing several places at once (docs/specs/place-list-multi-edit.md).

Covers the "Edit N Places…" context-menu action in src/place/place_list.py
and PlaceEditDialog's multi-place mode in src/place/place_edit.py.
"""

from pathlib import Path

from PySide6.QtWidgets import QMenu

from src.place import place_edit, place_list
from src.place.place_edit import PlaceEditDialog
from src.place.place_list import ListView


class _FakePlace:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None, place_latitude=None, place_longitude=None, place_description="", mbid=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = mbid
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.place_latitude = place_latitude
        self.place_longitude = place_longitude
        self.place_description = place_description


class _StubGet:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)

    def get_entity_object(self, model_name, **filters):
        assert model_name == "Place"
        place_id = filters.get("place_id")
        place_name = filters.get("place_name")
        for place in self._places:
            if place_id is not None and place.place_id == place_id:
                return place
            if place_name is not None and place.place_name == place_name:
                return place
        return None


class _StubUpdate:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def update_entities(self, model_name, entity_ids, **kwargs):
        self.calls.append((model_name, list(entity_ids), dict(kwargs)))
        return self.result


class _StubController:
    def __init__(self, places, update_result=True):
        self.get = _StubGet(places)
        self.update = _StubUpdate(update_result)


def _make_list_view(places, update_result=True):
    controller = _StubController(places, update_result=update_result)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view, controller


def _select_all(list_view):
    list_view.tree_widget.selectAll()


# ---- acceptance criteria 1-2: context-menu action visibility -----------


def test_edit_action_not_shown_for_single_selection(qapp, monkeypatch):
    place = _FakePlace(1, "Chicago")
    list_view, _ = _make_list_view([place])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        labels = [a.text() for a in built_menus[0].actions()]
        assert not any(label.startswith("Edit ") and "Places" in label for label in labels)
        assert "Edit" in labels
    finally:
        list_view.close()


def test_edit_n_places_action_shown_for_multi_selection(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago"), _FakePlace(2, "Denver")]
    list_view, _ = _make_list_view(places)
    _select_all(list_view)
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        labels = [a.text() for a in built_menus[0].actions()]
        assert "Edit 2 Places…" in labels
        assert "Delete 2 Places" in labels
    finally:
        list_view.close()


# ---- acceptance criteria 3-4: dialog shape in multi mode ----------------


def test_multi_dialog_title_and_fields(qapp):
    places = [_FakePlace(1, "Chicago"), _FakePlace(2, "Denver")]
    controller = _StubController(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.is_multi is True
        assert dialog.windowTitle() == "Edit 2 Places"
        assert not hasattr(dialog, "name_edit")
        assert not hasattr(dialog, "mbid_edit")
        assert not hasattr(dialog, "region_edit")
        assert hasattr(dialog, "type_edit")
        assert hasattr(dialog, "lat_edit")
        assert hasattr(dialog, "lon_edit")
        assert hasattr(dialog, "desc_edit")
        assert hasattr(dialog, "parent_edit")
    finally:
        dialog.close()


# ---- acceptance criteria 5-6: prefill on shared vs. mixed values --------


def test_multi_dialog_prefills_shared_type(qapp):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Denver", place_type="City")]
    controller = _StubController(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.type_edit.text() == "City"
    finally:
        dialog.close()


def test_multi_dialog_leaves_mixed_type_blank(qapp):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Illinois", place_type="State")]
    controller = _StubController(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.type_edit.text() == ""
    finally:
        dialog.close()


# ---- acceptance criterion 7: no touch -> no write ------------------------


def test_untouched_dialog_produces_no_changes(qapp):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Denver", place_type="City")]
    controller = _StubController(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        assert dialog.get_bulk_changes() == {}
    finally:
        dialog.close()


def test_edit_selected_places_no_op_when_nothing_changed(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago"), _FakePlace(2, "Denver")]
    list_view, controller = _make_list_view(places)
    _select_all(list_view)
    monkeypatch.setattr(PlaceEditDialog, "exec_", lambda self: PlaceEditDialog.Accepted)
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert controller.update.calls == []
        assert refreshed == []
    finally:
        list_view.close()


# ---- acceptance criteria 8-9: only touched fields are written -----------


def test_touching_only_type_field_updates_only_that_field(qapp):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Denver", place_type="City")]
    controller = _StubController(places)

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.type_edit.setText("Metro")
        dialog._mark_dirty("place_type")  # textEdited requires real key events; simulate directly

        changes = dialog.get_bulk_changes()

        assert changes == {"place_type": "Metro"}
    finally:
        dialog.close()


def test_edit_selected_places_calls_update_entities_with_only_touched_fields(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Denver", place_type="City")]
    list_view, controller = _make_list_view(places)
    _select_all(list_view)

    opened_dialogs = []

    def fake_exec(self):
        opened_dialogs.append(self)
        self.type_edit.setText("Metro")
        self._mark_dirty("place_type")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert len(controller.update.calls) == 1
        model_name, entity_ids, kwargs = controller.update.calls[0]
        assert model_name == "Place"
        assert sorted(entity_ids) == [1, 2]
        assert kwargs == {"place_type": "Metro"}
        assert refreshed == [True]
    finally:
        list_view.close()


# ---- acceptance criteria 10-11: validation still applies -----------------


def test_multi_dialog_rejects_non_numeric_latitude(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago"), _FakePlace(2, "Denver")]
    controller = _StubController(places)
    warnings = []
    monkeypatch.setattr(place_edit.QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.lat_edit.setText("not-a-number")
        dialog._mark_dirty("place_latitude")

        dialog.validate_and_accept()

        assert dialog.result() != PlaceEditDialog.Accepted
        assert any("Invalid Coordinates" in str(call) for call in warnings)
    finally:
        dialog.close()


def test_multi_dialog_rejects_unknown_parent_name(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago"), _FakePlace(2, "Denver")]
    controller = _StubController(places)
    warnings = []
    monkeypatch.setattr(place_edit.QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    dialog = PlaceEditDialog(controller, place=places)
    try:
        dialog.parent_edit.setText("Nowhere")
        dialog._mark_dirty("parent_id")

        changes = dialog.get_bulk_changes()

        assert changes is None
        assert any("Invalid Parent" in str(call) for call in warnings)
    finally:
        dialog.close()


# ---- acceptance criterion 13: batch-update failure surfaces an error ----


def test_edit_selected_places_shows_error_when_update_fails(qapp, monkeypatch):
    places = [_FakePlace(1, "Chicago", place_type="City"), _FakePlace(2, "Denver", place_type="City")]
    list_view, _controller = _make_list_view(places, update_result=False)
    _select_all(list_view)

    def fake_exec(self):
        self.type_edit.setText("Metro")
        self._mark_dirty("place_type")
        return PlaceEditDialog.Accepted

    monkeypatch.setattr(PlaceEditDialog, "exec_", fake_exec)
    criticals = []
    monkeypatch.setattr(place_list.QMessageBox, "critical", lambda *a, **k: criticals.append(a))
    refreshed = []
    list_view.parent_view = type("PV", (), {"refresh_views": lambda self: refreshed.append(True)})()
    try:
        list_view.edit_selected_places()

        assert len(criticals) == 1
        assert refreshed == []
    finally:
        list_view.close()


# ---- acceptance criterion 14: single-place editing is unaffected --------


def test_single_place_dialog_still_has_name_and_mbid_fields(qapp):
    place = _FakePlace(1, "Chicago", mbid="abc-123")
    controller = _StubController([place])

    dialog = PlaceEditDialog(controller, place=place)
    try:
        assert dialog.is_multi is False
        assert dialog.windowTitle() == "Edit Place"
        assert dialog.name_edit.text() == "Chicago"
        assert dialog.mbid_edit.text() == "abc-123"
        data = dialog.get_place_data()
        assert data["place_name"] == "Chicago"
        assert data["MBID"] == "abc-123"
    finally:
        dialog.close()


# ---- acceptance criterion 15: help guide documents bulk edit ------------


def test_help_guide_documents_bulk_edit():
    text = Path(__file__).resolve().parents[2] / "help_guide.md"
    content = text.read_text()
    places_section = content.split("## Places", 1)[1].split("## Publishers", 1)[0]
    assert "bulk-edit" in places_section.lower()
