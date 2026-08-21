"""Tests for exposing Louvain's full dendrogram (per-level partitions)
instead of the single best_partition() cut, and for filtering that
dendrogram down to levels worth surfacing in the UI.

See docs/specs/tiered_community_naming.md, acceptance criteria 1-2.
"""

from src.influences.influence_graph_algorithms import (
    assign_louvain_communities,
    filter_eligible_levels,
)


def _two_dense_clusters_bridged():
    """Two dense 4-node cliques joined by a single bridge edge -- Louvain
    should find 2 communities at every level (nothing to further merge/
    split at this size), giving a small, deterministic fixture."""
    edges = []
    for group in ([0, 1, 2, 3], [4, 5, 6, 7]):
        for i in group:
            for j in group:
                if i < j:
                    edges.append((i, j))
    edges.append((3, 4))  # bridge
    return list(range(8)), edges


def test_assign_louvain_communities_returns_full_dendrogram():
    node_ids, edges = _two_dense_clusters_bridged()
    levels = assign_louvain_communities(node_ids, edges)

    assert isinstance(levels, list)
    assert len(levels) >= 1
    for partition in levels:
        assert set(partition.keys()) == set(node_ids)

    finest = levels[0]
    assert finest[0] == finest[1] == finest[2] == finest[3]
    assert finest[4] == finest[5] == finest[6] == finest[7]
    assert finest[0] != finest[4]


def test_assign_louvain_communities_falls_back_on_error(monkeypatch):
    import src.influences.influence_graph_algorithms as algorithms

    class _BoomGraph:
        def add_nodes_from(self, *a, **k):
            raise TypeError("boom")

    monkeypatch.setattr(algorithms.nx, "Graph", lambda: _BoomGraph())
    levels = algorithms.assign_louvain_communities([1, 2, 3], [(1, 2)])
    assert levels == [{1: 0, 2: 0, 3: 0}]


def test_filter_eligible_levels_rejects_single_community():
    dendrogram = [{1: 0, 2: 0, 3: 0}]
    assert filter_eligible_levels(dendrogram) == []


def test_filter_eligible_levels_rejects_dominant_community():
    # 9 nodes in one community, 1 in another -- 90% > 80% dominance cap.
    partition = {i: 0 for i in range(9)}
    partition[9] = 1
    assert filter_eligible_levels([partition]) == []


def test_filter_eligible_levels_collapses_identical_adjacent_levels():
    level0 = {0: 0, 1: 0, 2: 1, 3: 1}
    level1 = {0: 0, 1: 0, 2: 1, 3: 1}  # same grouping, relabeled indices differ not at all here
    level2 = {0: 0, 1: 1, 2: 1, 3: 1}  # genuinely different grouping
    eligible = filter_eligible_levels([level0, level1, level2])
    assert eligible == [level0, level2]


def test_filter_eligible_levels_keeps_valid_multilevel_dendrogram():
    node_ids, edges = _two_dense_clusters_bridged()
    dendrogram = assign_louvain_communities(node_ids, edges)
    eligible = filter_eligible_levels(dendrogram)
    assert len(eligible) >= 1
    for partition in eligible:
        assert len(set(partition.values())) >= 2
