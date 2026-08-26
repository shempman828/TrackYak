"""Regression test: the place associations dialog used to force-expand
every entity-type group via expandAll() on load, so a place with many
associations dumped a huge flat list on screen with no way to narrow it
down. It now collapses large groups by default and offers a filter box.
"""

from types import SimpleNamespace

from src.place.place_assoc_details import (
    GROUP_AUTO_EXPAND_THRESHOLD,
    AssociationDetailsDialog,
)


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


class _StubGet:
    def __init__(self, associations):
        self._associations = associations

    def get_all_entities(self, model_name, **filters):
        if model_name == "PlaceAssociation":
            return self._associations
        return []

    def get_entity_object(self, entity_name, **filters):
        entity_id = next(iter(filters.values()))
        name_attr = f"{entity_name.lower()}_name"
        # Entity type is deliberately generic (not "artist"/"track"/etc.)
        # so create_entity_tooltip takes its fallback branch, which is all
        # this test's stub entities need to satisfy.
        return SimpleNamespace(**{name_attr: f"{entity_name} {entity_id}"})


class _StubController:
    def __init__(self, associations):
        self.get = _StubGet(associations)


def _build_dialog():
    # "gizmo"/"widget" are stand-ins for a real entity type (track, artist,
    # ...) that don't hit any of create_entity_tooltip's special-cased
    # branches, so the stub entity only needs a "<type>_name" attribute.
    many = [
        _StubAssociation("gizmo", i) for i in range(GROUP_AUTO_EXPAND_THRESHOLD + 5)
    ]
    few = [_StubAssociation("widget", i) for i in range(3)]
    controller = _StubController(many + few)
    place = _StubPlace(1, "Chicago")
    return AssociationDetailsDialog(controller, place)


def _group_items(dialog):
    tree = dialog.associations_tree
    return {
        tree.topLevelItem(i).text(0).split(" (")[0]: tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
    }


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
