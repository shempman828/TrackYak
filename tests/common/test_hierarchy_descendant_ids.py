"""Tests for hierarchy_descendant_ids and is_hierarchy_descendant
(src/common/widgets/hierarchy_tree_style.py) -- the single-BFS subtree
collector behind the genre parent pickers' cycle exclusion.
"""

from dataclasses import dataclass

from src.common.widgets.hierarchy_tree_style import hierarchy_descendant_ids, is_hierarchy_descendant


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
