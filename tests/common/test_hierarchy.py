"""Hierarchy helpers: descendant-id collection and box-drawing tree render."""

from dataclasses import dataclass

from src.common.hierarchy_ops import hierarchy_descendant_ids, insert_as_new_child, insert_as_new_parent, is_hierarchy_descendant, render_hierarchy_as_text

# ---------------------------------------------------------------------------
# Tests for hierarchy_descendant_ids and is_hierarchy_descendant
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    id: int
    parent_id: int | None = None


def _ids(root_ids, nodes):
    return hierarchy_descendant_ids(root_ids, nodes, id_attr="id")


def test_collects_descendants_at_every_depth():
    nodes = [_Node(1), _Node(2, 1), _Node(3, 2), _Node(4, 3), _Node(5)]

    assert _ids([1], nodes) == {2, 3, 4}


def test_excludes_roots_and_unrelated_nodes():
    nodes = [_Node(1), _Node(2, 1), _Node(5), _Node(6, 5)]

    assert _ids([1], nodes) == {2}


def test_unions_subtrees_of_multiple_roots():
    nodes = [_Node(1), _Node(2, 1), _Node(5), _Node(6, 5), _Node(7, 6), _Node(9)]

    assert _ids([1, 5], nodes) == {2, 6, 7}


def test_root_nested_under_another_root_is_reported_as_descendant():
    nodes = [_Node(1), _Node(2, 1), _Node(3, 2)]

    assert _ids([1, 2], nodes) == {2, 3}


def test_cyclic_hierarchy_terminates():
    nodes = [_Node(1, 3), _Node(2, 1), _Node(3, 2)]

    assert _ids([1], nodes) == {1, 2, 3}
    assert is_hierarchy_descendant(1, 3, nodes, id_attr="id")


def test_is_hierarchy_descendant_matches_subtree_membership():
    nodes = [_Node(1), _Node(2, 1), _Node(3, 2), _Node(5)]

    assert is_hierarchy_descendant(1, 3, nodes, id_attr="id")
    assert not is_hierarchy_descendant(3, 1, nodes, id_attr="id")
    assert not is_hierarchy_descendant(1, 5, nodes, id_attr="id")
    assert not is_hierarchy_descendant(None, 3, nodes, id_attr="id")


# ---------------------------------------------------------------------------
# Tests for docs/specs/genre_hierarchy_export.md's
# ---------------------------------------------------------------------------


@dataclass
class _NodeRender:
    id: int
    name: str
    parent_id: int | None = None


def _render(nodes, sort_key=None):
    return render_hierarchy_as_text(nodes, id_attr="id", name_attr="name", parent_attr="parent_id", sort_key=sort_key)


def test_single_root_no_children():
    nodes = [_NodeRender(1, "Other")]
    assert _render(nodes) == "Other"


def test_sample_data_matches_expected_box_drawing_format():
    nodes = [
        _NodeRender(1, "Other"),
        _NodeRender(2, "Singer & Songwriter", parent_id=1),
        _NodeRender(3, "Spoken Word", parent_id=1),
        _NodeRender(4, "Comedy", parent_id=1),
        _NodeRender(5, "Children's", parent_id=1),
        _NodeRender(6, "Soundtrack", parent_id=1),
        _NodeRender(7, "Film Score", parent_id=6),
        _NodeRender(8, "Musical", parent_id=6),
        _NodeRender(9, "Avant-Garde", parent_id=1),
        _NodeRender(10, "Experimental", parent_id=9),
        _NodeRender(11, "Noise", parent_id=9),
        _NodeRender(12, "Leftfield", parent_id=9),
    ]

    expected = "\n".join(
        [
            "Other",
            "├── Singer & Songwriter",
            "├── Spoken Word",
            "├── Comedy",
            "├── Children's",
            "├── Soundtrack",
            "│   ├── Film Score",
            "│   └── Musical",
            "└── Avant-Garde",
            "    ├── Experimental",
            "    ├── Noise",
            "    └── Leftfield",
        ]
    )

    assert _render(nodes) == expected


def test_multiple_roots_each_start_unprefixed_block_in_given_order():
    nodes = [_NodeRender(1, "Other"), _NodeRender(2, "Rock"), _NodeRender(3, "Child of Rock", parent_id=2)]

    result = _render(nodes)
    assert result == "Other\nRock\n└── Child of Rock"


def test_multiple_roots_ordered_by_sort_key():
    nodes = [_NodeRender(1, "Zydeco"), _NodeRender(2, "Alpha")]

    result = _render(nodes, sort_key=lambda n: n.name.lower())
    assert result == "Alpha\nZydeco"


def test_empty_input_returns_empty_string():
    assert _render([]) == ""


def test_render_treats_orphans_as_roots_and_tolerates_null_names():
    nodes = [_NodeRender(1, "Rock"), _NodeRender(2, "Lost", parent_id=99), _NodeRender(3, None, parent_id=1)]

    assert _render(nodes) == "Rock\n└── \nLost"


# ---------------------------------------------------------------------------
# insert_as_new_parent / insert_as_new_child: update_entity returns False on failure
# ---------------------------------------------------------------------------


class _StubUpdate:
    def __init__(self, fail_on_call=None):
        self.calls = []
        self._fail_on_call = fail_on_call

    def update_entity(self, entity_type, entity_id, **kwargs):
        self.calls.append((entity_id, kwargs))
        return len(self.calls) != self._fail_on_call


class _StubController:
    def __init__(self, fail_on_call=None):
        self.update = _StubUpdate(fail_on_call)


def test_insert_as_new_parent_undoes_first_step_when_second_fails():
    controller = _StubController(fail_on_call=2)
    entity = _Node(1, parent_id=7)
    new_entity = _Node(2, parent_id=None)

    assert insert_as_new_parent(controller, "Genre", "id", entity, new_entity) is False
    assert controller.update.calls == [(2, {"parent_id": 7}), (1, {"parent_id": 2}), (2, {"parent_id": None})]


def test_insert_as_new_child_reports_failure():
    assert insert_as_new_child(_StubController(fail_on_call=1), "Genre", "id", _Node(1), _Node(2)) is False
    assert insert_as_new_child(_StubController(), "Genre", "id", _Node(1), _Node(2)) is True
