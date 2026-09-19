"""Tests for the "Clear Filters" button in the place list's filter row."""

from src.place.place_list import ListView


class _FakePlace:
    def __init__(self, place_id, place_name, place_type="City", parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.MBID = None
        self.parent_id = parent_id
        self.association_count = 0
        self.recursive_association_count = 0
        self.place_latitude = None
        self.place_longitude = None
        self.place_description = ""


class _StubGet:
    def __init__(self, places):
        self._places = places

    def get_all_entities(self, model_name, **filters):
        assert model_name == "Place"
        return list(self._places)


class _StubController:
    def __init__(self, places):
        self.get = _StubGet(places)


def _make_list_view(qapp):
    places = [
        _FakePlace(1, "USA", place_type="Country"),
        _FakePlace(2, "California", place_type="State", parent_id=1),
        _FakePlace(3, "Los Angeles", place_type="City", parent_id=2),
    ]
    controller = _StubController(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_clear_filters_button_exists_with_tooltip(qapp):
    list_view = _make_list_view(qapp)
    try:
        assert list_view.clear_filters_button.text() == "Clear Filters"
        assert list_view.clear_filters_button.toolTip() == "Reset all filters"
    finally:
        list_view.close()


def test_clear_filters_resets_search_text(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.search_bar.setText("Los Angeles")
        assert list_view.filter_text == "Los Angeles"

        list_view.clear_filters_button.click()

        assert list_view.search_bar.text() == ""
        assert list_view.filter_text == ""
    finally:
        list_view.close()


def test_clear_filters_reselects_all_types(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.type_filter_widget.select_none()
        assert list_view.selected_types == set()

        list_view.clear_filters_button.click()

        assert list_view.selected_types == list_view.all_place_types
    finally:
        list_view.close()


def test_clear_filters_unchecks_mbid_and_coords_checkboxes(qapp):
    list_view = _make_list_view(qapp)
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
    list_view = _make_list_view(qapp)
    try:
        list_view.search_bar.setText("USA")
        assert list_view.tree_widget.count_visible() < list_view.tree_widget.count_total()

        list_view.clear_filters_button.click()

        assert list_view.tree_widget.count_visible() == list_view.tree_widget.count_total()
    finally:
        list_view.close()
