"""Place associations: association rows and their detail text."""

from types import SimpleNamespace

from src.place.place_assoc_details import GROUP_AUTO_EXPAND_THRESHOLD, AssociationDetailsDialog
from src.place.place_associations import fetch_entities, fetch_place_associations, group_associations

# ---------------------------------------------------------------------------
# Tests for src/place/place_associations.py: recursive paths and batched entity queries.
# ---------------------------------------------------------------------------


def _assoc(place_id, entity_type, entity_id):
    return SimpleNamespace(place_id=place_id, entity_type=entity_type, entity_id=entity_id, association_type=None)


class _StubGet:
    def __init__(self, places, associations):
        self.places = places
        self.associations = associations
        self.calls = []

    def get_all_entities(self, model_name, load_options=None, **filters):
        self.calls.append((model_name, filters))
        if model_name == "Place":
            return self.places
        if model_name == "PlaceAssociation":
            if "place_id__in" in filters:
                return [a for a in self.associations if a.place_id in filters["place_id__in"]]
            return [a for a in self.associations if a.place_id == filters["place_id"]]
        ids = filters[f"{model_name.lower()}_id__in"]
        return [SimpleNamespace(**{f"{model_name.lower()}_id": i, f"{model_name.lower()}_name": f"{model_name} {i}"}) for i in ids]


def _controller():
    places = [
        SimpleNamespace(place_id=1, place_name="UK", parent_id=None),
        SimpleNamespace(place_id=2, place_name="London", parent_id=1),
        SimpleNamespace(place_id=3, place_name="Camden", parent_id=2),
        SimpleNamespace(place_id=4, place_name="France", parent_id=None),
    ]
    associations = [_assoc(1, "Artist", 10), _assoc(3, "Track", 20), _assoc(3, "Track", 21), _assoc(4, "Track", 22)]
    return SimpleNamespace(get=_StubGet(places, associations))


def test_recursive_fetch_tags_each_row_with_its_path_and_uses_one_association_query():
    controller = _controller()

    rows = fetch_place_associations(controller, 1, recursive=True)

    assert sorted((a.entity_id, a.place_path) for a in rows) == [(10, "UK"), (20, "UK → London → Camden"), (21, "UK → London → Camden")]
    assert [c[0] for c in controller.get.calls].count("PlaceAssociation") == 1


def test_fetch_entities_queries_each_entity_type_once():
    controller = _controller()
    rows = fetch_place_associations(controller, 1, recursive=True)
    controller.get.calls.clear()

    entities = fetch_entities(controller, rows)

    assert sorted(c[0] for c in controller.get.calls) == ["Artist", "Track"]
    assert entities[("Track", 21)].track_name == "Track 21"


def test_group_associations_groups_by_type():
    groups = group_associations(_controller(), 3)
    assert list(groups) == ["Track"]
    assert [entity.track_id for _assoc, entity in groups["Track"]] == [20, 21]


# ---------------------------------------------------------------------------
# Regression test: the place associations dialog used to force-expand
# ---------------------------------------------------------------------------


class _StubPlace:
    def __init__(self, place_id, place_name):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = "City"


class _StubAssociation:
    def __init__(self, entity_type, entity_id, type_name="Related"):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.association_type = SimpleNamespace(type_name=type_name)


class _StubGetDetails:
    def __init__(self, associations):
        self._associations = associations

    def get_all_entities(self, model_name, load_options=None, **filters):
        if model_name == "PlaceAssociation":
            return self._associations
        # Batched entity fetch: "<type>_id__in". The entity type is deliberately
        # generic (not "artist"/"track"/etc.) so entity_tooltip takes its
        # fallback branch, which is all these stub entities need.
        prefix = model_name.lower()
        ids = filters.get(f"{prefix}_id__in", [])
        return [SimpleNamespace(**{f"{prefix}_id": i, f"{prefix}_name": f"{model_name} {i}"}) for i in ids]


class _StubController:
    def __init__(self, associations):
        self.get = _StubGetDetails(associations)


def _build_dialog():
    # "gizmo"/"widget" are stand-ins for a real entity type (track, artist,
    # ...) that don't hit any of create_entity_tooltip's special-cased
    # branches, so the stub entity only needs a "<type>_name" attribute.
    many = [_StubAssociation("gizmo", i) for i in range(GROUP_AUTO_EXPAND_THRESHOLD + 5)]
    few = [_StubAssociation("widget", i) for i in range(3)]
    controller = _StubController(many + few)
    place = _StubPlace(1, "Chicago")
    return AssociationDetailsDialog(controller, place)


def _group_items(dialog):
    tree = dialog.associations_tree
    return {tree.topLevelItem(i).text(0).split(" (")[0]: tree.topLevelItem(i) for i in range(tree.topLevelItemCount())}


def test_large_group_starts_collapsed_small_group_starts_expanded(qapp):
    dialog = _build_dialog()
    try:
        groups = _group_items(dialog)
        assert groups["Gizmos"].isExpanded() is False
        assert groups["Widgets"].isExpanded() is True
    finally:
        dialog.close()


def test_filter_narrows_to_matching_group_and_expands_it(qapp):
    dialog = _build_dialog()
    try:
        dialog.filter_edit.setText("widget 1")

        groups = _group_items(dialog)
        assert groups["Widgets"].isHidden() is False
        assert groups["Widgets"].isExpanded() is True
        assert groups["Gizmos"].isHidden() is True

        dialog.filter_edit.setText("")

        groups = _group_items(dialog)
        assert groups["Gizmos"].isHidden() is False
        assert groups["Gizmos"].isExpanded() is False
        assert groups["Widgets"].isExpanded() is True
    finally:
        dialog.close()
