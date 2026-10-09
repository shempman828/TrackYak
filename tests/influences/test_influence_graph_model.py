"""Tests for the graph-model mixins: off-thread result building, node sizing, incremental adds, and busy guards."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.influences.graph.influence_graph_data import GraphResult, InfluenceGraphDataMixin, dedupe_edges, degree_mass
from src.influences.graph.influence_graph_legend import InfluenceGraphLegendMixin
from src.influences.graph.influence_graph_render import InfluenceGraphRenderMixin


class _Host(InfluenceGraphDataMixin, InfluenceGraphRenderMixin, InfluenceGraphLegendMixin):
    """Real data/render/legend logic with JS and signals captured instead of a web view."""

    def __init__(self, controller=None):
        self.controller = controller or MagicMock()
        self.node_names = {}
        self.node_aliases = {}
        self.edges = []
        self.node_mass = {}
        self.community_levels = []
        self.active_level = None
        self.community_id = {}
        self.community_names = {}
        self.community_names_by_level = {}
        self.influence_scores = {}
        self.page_rank_scores = {}
        self.combined_scores = {}
        self._graph_worker = None
        self.js_calls = []
        self.graph_updated = MagicMock()
        self.display_global_network = MagicMock()

    def _run_js(self, code):
        self.js_calls.append(code)

    def fetch_node_aliases(self, node_ids):
        return {}


def _added_elements(host):
    calls = [c for c in host.js_calls if c.startswith("addElements(")]
    assert len(calls) == 1
    return json.loads(calls[0][len("addElements(") : -1])


def test_dedupe_edges_drops_duplicates_and_unknown_endpoints():
    assert dedupe_edges([(1, 2), (1, 2), (2, 9), (2, 1)], {1, 2}) == [(1, 2), (2, 1)]


def test_degree_mass_counts_both_endpoints():
    assert degree_mass([1, 2, 3], [(1, 2), (1, 3)]) == {1: 3, 2: 2, 3: 2}


def test_compute_graph_result_does_not_touch_view_state():
    # The worker thread must only read; the main thread applies the result.
    controller = MagicMock()
    host = _Host(controller)
    host.extract_global_graph = lambda: ([(1, "A"), (2, "B"), (3, "C")], [(1, 2), (1, 2), (2, 3)])

    result = host._compute_graph_result()

    assert host.node_names == {}
    assert host.edges == []
    assert result.node_names == {1: "A", 2: "B", 3: "C"}
    assert result.edges == [(1, 2), (2, 3)]
    assert result.influence_scores[1] == 2
    assert result.community_levels


def test_compute_graph_result_empty_graph_is_none():
    host = _Host()
    host.extract_global_graph = lambda: ([], [])
    assert host._compute_graph_result() is None


def test_apply_graph_result_keeps_valid_active_level_and_clamps_stale_one():
    host = _Host()
    levels = [{1: 0, 2: 1}, {1: 0, 2: 0}]
    host.active_level = 0
    host._apply_graph_result(GraphResult(node_names={1: "A", 2: "B"}, community_levels=levels))
    assert host.active_level == 0
    assert host.community_id == levels[0]

    host.active_level = 5
    host._apply_graph_result(GraphResult(node_names={1: "A", 2: "B"}, community_levels=levels))
    assert host.active_level == 1


def test_apply_empty_result_clears_model():
    host = _Host()
    host.node_names = {1: "A"}
    host.active_level = 0
    host._apply_graph_result(None)
    assert host.node_names == {}
    assert host.active_level is None
    assert host.community_id == {}


def test_get_node_size_clamps_scores_above_cap():
    host = _Host()
    host.influence_scores = {1: 48, 2: 5000}
    assert host.get_node_size(2) == host.get_node_size(1) == 240


def test_push_graph_on_empty_model_clears_canvas():
    host = _Host()
    host._push_graph()
    assert host.js_calls == ["clearGraph()"]


def test_add_influence_adds_missing_endpoint_and_edge_in_one_call():
    # An existing artist with no prior relationships is not on the canvas yet;
    # the edge used to be dropped silently.
    host = _Host()
    host.node_names = {1: "Miles Davis"}
    host.community_id = {1: 0}

    host.add_influence((1, "Miles Davis"), (7, "Brand New"))

    ids = [e["data"]["id"] for e in _added_elements(host)]
    assert "7" in ids
    assert "e1_7" in ids
    assert "1" not in ids
    assert (1, 7) in host.edges
    assert host.node_names[7] == "Brand New"
    host.graph_updated.emit.assert_called_once()


def test_add_influence_on_empty_graph_rebuilds():
    host = _Host()
    host.add_influence((1, "A"), (2, "B"))
    host.display_global_network.assert_called_once()
    assert host.js_calls == []


def test_add_single_artist_does_not_query_db():
    controller = MagicMock()
    host = _Host(controller)
    host.node_names = {1: "A"}
    host.community_id = {1: 0}

    host.add_single_artist(2, "B")

    controller.get.get_all_entities.assert_not_called()
    assert 2 in host.node_names


def test_level_change_ignored_while_recomputing():
    host = _Host()
    host.community_levels = [{1: 0}, {1: 0}]
    host.active_level = 0
    host._graph_worker = SimpleNamespace()
    host._push_graph = MagicMock()

    host._on_level_changed(1)

    assert host.active_level == 0
    host._push_graph.assert_not_called()
