"""Place list view: expand/collapse, state kept on reload, filters (clear,
no-parent), context actions, and Find Duplicates.
"""

from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from src.place import place_list
from src.place.dialogs.place_edit import PlaceEditDialog
from src.place.place_list import ListView

# ---------------------------------------------------------------------------
# Tests for ListView place actions: failed writes, delete reparenting, and new parent/child places.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests for the Expand All / Collapse All buttons in the place list's
# ---------------------------------------------------------------------------


class _FakePlaceCollapse:
    def __init__(self, place_id, place_name, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGetCollapse:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubController:
    def __init__(self, places):
        self.get = _StubGetCollapse(places)


def _make_list_view(qapp):
    places = [_FakePlaceCollapse(1, "USA"), _FakePlaceCollapse(2, "California", parent_id=1), _FakePlaceCollapse(3, "Los Angeles", parent_id=2)]
    controller = _StubController(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_expand_all_opens_every_branch(qapp):
    list_view = _make_list_view(qapp)
    try:
        top_item = list_view.tree_widget.topLevelItem(0)
        assert not top_item.isExpanded()

        list_view.expand_all_button.click()

        assert top_item.isExpanded()
        assert top_item.child(0).isExpanded()
    finally:
        list_view.close()


def test_collapse_all_closes_every_branch(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.tree_widget.expandAll()
        top_item = list_view.tree_widget.topLevelItem(0)
        assert top_item.isExpanded()

        list_view.collapse_all_button.click()

        assert not top_item.isExpanded()
        assert not top_item.child(0).isExpanded()
    finally:
        list_view.close()


def test_expand_collapse_buttons_disabled_in_flat_view(qapp):
    list_view = _make_list_view(qapp)
    try:
        assert list_view.expand_all_button.isEnabled()
        assert list_view.collapse_all_button.isEnabled()

        list_view.view_mode_control.setCurrentIndex(1)  # Flat

        assert not list_view.expand_all_button.isEnabled()
        assert not list_view.collapse_all_button.isEnabled()

        list_view.view_mode_control.setCurrentIndex(0)  # Tree

        assert list_view.expand_all_button.isEnabled()
        assert list_view.collapse_all_button.isEnabled()
    finally:
        list_view.close()


# ---------------------------------------------------------------------------
# Regression test: editing a place (or any other action that calls
# ---------------------------------------------------------------------------


class _FakePlaceReload:
    def __init__(self, place_id, place_name, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGetReload:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        # Return fresh Place instances each call, like a real DB fetch would —
        # object identity must not be what state-restore relies on.
        return [_FakePlaceReload(p.place_id, p.place_name, p.parent_id) for p in self._places]


class _StubControllerReload:
    def __init__(self, places):
        self.get = _StubGetReload(places)


def _make_list_view_reload(qapp):
    places = [_FakePlaceReload(1, "USA"), _FakePlaceReload(2, "California", parent_id=1), _FakePlaceReload(3, "Los Angeles", parent_id=2)]
    controller = _StubControllerReload(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_reload_keeps_expanded_branches_open(qapp):
    list_view = _make_list_view_reload(qapp)
    try:
        top_item = list_view.tree_widget.topLevelItem(0)
        top_item.setExpanded(True)
        top_item.child(0).setExpanded(True)

        list_view.load_places()

        top_item = list_view.tree_widget.topLevelItem(0)
        assert top_item.isExpanded()
        assert top_item.child(0).isExpanded()
    finally:
        list_view.close()


def test_reload_keeps_current_selection(qapp):
    list_view = _make_list_view_reload(qapp)
    try:
        top_item = list_view.tree_widget.topLevelItem(0)
        top_item.setExpanded(True)
        target_item = top_item.child(0)
        list_view.tree_widget.setCurrentItem(target_item)

        list_view.load_places()

        current_item = list_view.tree_widget.currentItem()
        assert current_item is not None
        assert current_item.data(0, list_view._ID_ROLE) == 2
    finally:
        list_view.close()


def test_initial_load_still_starts_fully_collapsed(qapp):
    list_view = _make_list_view_reload(qapp)
    try:
        top_item = list_view.tree_widget.topLevelItem(0)
        assert not top_item.isExpanded()
    finally:
        list_view.close()


# ---------------------------------------------------------------------------
# Tests for the "Clear Filters" button in the place list's filter row.
# ---------------------------------------------------------------------------


class _FakePlaceFilters:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGetFilters:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubControllerFilters:
    def __init__(self, places):
        self.get = _StubGetFilters(places)


def _make_list_view_filters(qapp):
    places = [
        _FakePlaceFilters(1, "USA", place_type="Country"),
        _FakePlaceFilters(2, "California", place_type="State", parent_id=1),
        _FakePlaceFilters(3, "Los Angeles", place_type="City", parent_id=2),
    ]
    controller = _StubControllerFilters(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_clear_filters_button_exists_with_tooltip(qapp):
    list_view = _make_list_view_filters(qapp)
    try:
        assert list_view.clear_filters_button.text() == "Clear Filters"
        assert list_view.clear_filters_button.toolTip() == "Reset all filters"
    finally:
        list_view.close()


def test_clear_filters_resets_search_text(qapp):
    list_view = _make_list_view_filters(qapp)
    try:
        list_view.search_bar.setText("Los Angeles")
        list_view.filter_places("Los Angeles")
        assert list_view.filter_text == "Los Angeles"

        list_view.clear_filters_button.click()

        assert list_view.search_bar.text() == ""
        assert list_view.filter_text == ""
    finally:
        list_view.close()


def test_clear_filters_reselects_all_types(qapp):
    list_view = _make_list_view_filters(qapp)
    try:
        list_view.type_filter_widget.select_none()
        assert list_view.selected_types == set()

        list_view.clear_filters_button.click()

        assert list_view.selected_types == list_view.all_place_types
    finally:
        list_view.close()


def test_clear_filters_unchecks_mbid_and_coords_checkboxes(qapp):
    list_view = _make_list_view_filters(qapp)
    try:
        list_view.mbid_missing_checkbox.setChecked(True)
        list_view.coords_missing_checkbox.setChecked(True)
        assert list_view.mbid_missing_only is True
        assert list_view.coords_missing_only is True

        list_view.clear_filters_button.click()

        assert not list_view.mbid_missing_checkbox.isChecked()
        assert not list_view.coords_missing_checkbox.isChecked()
        assert list_view.mbid_missing_only is False
        assert list_view.coords_missing_only is False
    finally:
        list_view.close()


def test_clear_filters_shows_all_places_again(qapp):
    list_view = _make_list_view_filters(qapp)
    try:
        list_view.search_bar.setText("USA")
        list_view.filter_places("USA")
        assert list_view.tree_widget.count_items()[1] < list_view.tree_widget.count_items()[0]

        list_view.clear_filters_button.click()

        assert list_view.tree_widget.count_items()[1] == list_view.tree_widget.count_items()[0]
    finally:
        list_view.close()


# ---------------------------------------------------------------------------
# Tests for the "No parent" checkbox in the place list's filter row.
# ---------------------------------------------------------------------------


class _FakePlaceFilter:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGetFilter:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubControllerFilter:
    def __init__(self, places):
        self.get = _StubGetFilter(places)


def _make_list_view_filter(qapp):
    places = [
        _FakePlaceFilter(1, "USA", place_type="Country"),
        _FakePlaceFilter(2, "California", place_type="State", parent_id=1),
        _FakePlaceFilter(3, "Los Angeles", place_type="City", parent_id=2),
        _FakePlaceFilter(4, "Canada", place_type="Country"),
    ]
    controller = _StubControllerFilter(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_no_parent_checkbox_exists_with_tooltip(qapp):
    list_view = _make_list_view_filter(qapp)
    try:
        assert list_view.no_parent_checkbox.text() == "No parent"
        assert list_view.no_parent_checkbox.toolTip() == "Show only top-level places (places with no parent)"
    finally:
        list_view.close()


def test_checking_no_parent_shows_only_top_level_places(qapp):
    list_view = _make_list_view_filter(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        assert list_view.no_parent_only is True
        # Only USA and Canada have no parent
        assert list_view.tree_widget.count_items()[1] == 2
    finally:
        list_view.close()


def test_unchecking_no_parent_shows_all_places_again(qapp):
    list_view = _make_list_view_filter(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        list_view.no_parent_checkbox.setChecked(False)

        assert list_view.no_parent_only is False
        assert list_view.tree_widget.count_items()[1] == list_view.tree_widget.count_items()[0]
    finally:
        list_view.close()


def test_clear_filters_unchecks_no_parent_checkbox(qapp):
    list_view = _make_list_view_filter(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        assert list_view.no_parent_only is True

        list_view.clear_filters_button.click()

        assert not list_view.no_parent_checkbox.isChecked()
        assert list_view.no_parent_only is False
        assert list_view.tree_widget.count_items()[1] == list_view.tree_widget.count_items()[0]
    finally:
        list_view.close()


def test_no_parent_filter_combines_with_search_text(qapp):
    list_view = _make_list_view_filter(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        list_view.search_bar.setText("Canada")
        list_view.filter_places("Canada")

        assert list_view.tree_widget.count_items()[1] == 1
    finally:
        list_view.close()


# ---------------------------------------------------------------------------
# Tests for the "Find Duplicate Places…" context-menu action wiring
# ---------------------------------------------------------------------------


class _FakePlaceDuplicates:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.associations = []
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGetDuplicates:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubControllerDuplicates:
    def __init__(self, places):
        self.get = _StubGetDuplicates(places)


def _make_list_view_duplicates(qapp, places=None):
    places = places if places is not None else [_FakePlaceDuplicates(1, "USA", place_type="Country")]
    controller = _StubControllerDuplicates(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_find_duplicates_action_shown_when_no_item_under_cursor(qapp, monkeypatch):
    list_view = _make_list_view_duplicates(qapp, places=[])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        list_view.show_context_menu(list_view.tree_widget.rect().center())

        assert len(built_menus) == 1
        labels = [a.text() for a in built_menus[0].actions()]
        assert "🔎 Find Duplicate Places…" in labels
    finally:
        list_view.close()


def test_find_duplicates_action_shown_alongside_item_actions(qapp, monkeypatch):
    place = _FakePlaceDuplicates(1, "USA", place_type="Country")
    list_view = _make_list_view_duplicates(qapp, places=[place])
    built_menus = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))
    try:
        item = list_view.tree_widget.topLevelItem(0)
        position = list_view.tree_widget.visualItemRect(item).center()

        list_view.show_context_menu(position)

        assert len(built_menus) == 1
        labels = [a.text() for a in built_menus[0].actions()]
        assert "🔎 Find Duplicate Places…" in labels
        assert "Merge" in labels
    finally:
        list_view.close()


def test_find_fuzzy_matches_with_no_places_shows_status_message(qapp):
    list_view = ListView(_StubControllerDuplicates([]))
    try:
        with patch("src.place.place_list.show_status_message") as mock_status:
            list_view.find_fuzzy_matches()

        mock_status.assert_called_once()
        assert "No places found" in mock_status.call_args[0][1]
    finally:
        list_view.close()


def test_find_fuzzy_matches_with_no_similar_pairs_shows_status_message(qapp):
    places = [_FakePlaceDuplicates(1, "Tokyo", place_type="City"), _FakePlaceDuplicates(2, "Berlin", place_type="City")]
    list_view = _make_list_view_duplicates(qapp, places=places)
    try:
        with patch("src.place.place_list.show_status_message") as mock_status:
            list_view.find_fuzzy_matches()
            list_view._fuzzy_worker.wait(5000)
            QApplication.processEvents()

        mock_status.assert_called_once()
        assert "No similar place names found" in mock_status.call_args[0][1]
    finally:
        list_view.close()
