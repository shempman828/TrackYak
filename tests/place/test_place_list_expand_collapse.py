"""Tests for the Expand All / Collapse All buttons in the place list's
tree view, and for disabling them while the list is showing as a flat,
unnested list.
"""

from src.place.place_list import ListView


class _FakePlace:
    def __init__(self, place_id, place_name, parent_id=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"
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
        _FakePlace(1, "USA"),
        _FakePlace(2, "California", parent_id=1),
        _FakePlace(3, "Los Angeles", parent_id=2),
    ]
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

        list_view.flat_view_button.setChecked(True)
        list_view.toggle_flat_view()

        assert not list_view.expand_all_button.isEnabled()
        assert not list_view.collapse_all_button.isEnabled()

        list_view.flat_view_button.setChecked(False)
        list_view.toggle_flat_view()

        assert list_view.expand_all_button.isEnabled()
        assert list_view.collapse_all_button.isEnabled()
    finally:
        list_view.close()
