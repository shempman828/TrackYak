"""Tests for the "No parent" checkbox in the place list's filter row."""

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
        _FakePlace(4, "Canada", place_type="Country"),
    ]
    controller = _StubController(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_no_parent_checkbox_exists_with_tooltip(qapp):
    list_view = _make_list_view(qapp)
    try:
        assert list_view.no_parent_checkbox.text() == "No parent"
        assert list_view.no_parent_checkbox.toolTip() == "Show only top-level places (places with no parent)"
    finally:
        list_view.close()


def test_checking_no_parent_shows_only_top_level_places(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        assert list_view.no_parent_only is True
        # Only USA and Canada have no parent
        assert list_view.tree_widget.count_visible() == 2
    finally:
        list_view.close()


def test_unchecking_no_parent_shows_all_places_again(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        list_view.no_parent_checkbox.setChecked(False)

        assert list_view.no_parent_only is False
        assert list_view.tree_widget.count_visible() == list_view.tree_widget.count_total()
    finally:
        list_view.close()


def test_clear_filters_unchecks_no_parent_checkbox(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        assert list_view.no_parent_only is True

        list_view.clear_filters_button.click()

        assert not list_view.no_parent_checkbox.isChecked()
        assert list_view.no_parent_only is False
        assert list_view.tree_widget.count_visible() == list_view.tree_widget.count_total()
    finally:
        list_view.close()


def test_no_parent_filter_combines_with_search_text(qapp):
    list_view = _make_list_view(qapp)
    try:
        list_view.no_parent_checkbox.setChecked(True)
        list_view.search_bar.setText("Canada")

        assert list_view.tree_widget.count_visible() == 1
    finally:
        list_view.close()
