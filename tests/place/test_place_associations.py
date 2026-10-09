"""Tests for src/place/place_associations.py: recursive paths and batched entity queries."""

from types import SimpleNamespace

from src.place.place_associations import fetch_entities, fetch_place_associations, group_associations


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
