"""Regression test: editing a place (or any other action that calls
load_places()) must not reset which branches are expanded or which place
is selected. Before the fix, load_places() cleared the tree and rebuilt it
from scratch with no state saved, so every reload jumped the user back to
a fully collapsed, unselected top of the list.
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
        # Return fresh Place instances each call, like a real DB fetch would —
        # object identity must not be what state-restore relies on.
        return [_FakePlace(p.place_id, p.place_name, p.parent_id) for p in self._places]


class _StubController:
    def __init__(self, places):
        self.get = _StubGet(places)


def _make_list_view(qapp):
    places = [_FakePlace(1, "USA"), _FakePlace(2, "California", parent_id=1), _FakePlace(3, "Los Angeles", parent_id=2)]
    controller = _StubController(places)
    list_view = ListView(controller)
    list_view.load_places()
    return list_view


def test_reload_keeps_expanded_branches_open(qapp):
    list_view = _make_list_view(qapp)
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
    list_view = _make_list_view(qapp)
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
    list_view = _make_list_view(qapp)
    try:
        top_item = list_view.tree_widget.topLevelItem(0)
        assert not top_item.isExpanded()
    finally:
        list_view.close()
