"""Graph extraction, scoring, and node sizing for InfluenceGraphView (no Qt/JS)."""

from collections import Counter
from dataclasses import dataclass, field
import math

from src.foundation.logger_config import logger
from src.influences.graph import influence_graph_algorithms as algorithms


@dataclass
class GraphResult:
    """Everything one background recompute produces, applied to the view on the main thread."""

    node_names: dict = field(default_factory=dict)
    node_aliases: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    node_mass: dict = field(default_factory=dict)
    community_levels: list = field(default_factory=list)
    influence_scores: dict = field(default_factory=dict)
    page_rank_scores: dict = field(default_factory=dict)
    combined_scores: dict = field(default_factory=dict)


def dedupe_edges(edges, node_id_set):
    """Return `edges` without duplicates or endpoints outside `node_id_set`, order kept."""
    seen = set()
    result = []
    for edge in edges:
        a, b = edge
        if a in node_id_set and b in node_id_set and edge not in seen:
            seen.add(edge)
            result.append((a, b))
    return result


def degree_mass(node_ids, edges):
    """Return {node_id: 1 + degree}, used to rank representative artists."""
    degree = Counter()
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    return {node_id: 1 + degree.get(node_id, 0) for node_id in node_ids}


class InfluenceGraphDataMixin:
    """Graph data adapter; the host provides self.controller and the graph-model attributes."""

    # Score that maps to max node size; higher scores clamp to max_size.
    _SIZE_SCORE_CAP = 48

    # Cytoscape draws labels in graph units, and the ~600-node overview fits at
    # ~0.1 zoom, so fonts are scaled up to stay legible at a moderate zoom-in.
    _FONT_SCALE = 3.5

    # -----------------------
    # Graph extraction
    # -----------------------
    def extract_global_graph(self):
        """Return (nodes, edges) for every artist in an influence relationship."""
        return algorithms.extract_global_influence_graph(self.controller.get)

    def fetch_node_aliases(self, node_ids):
        """Return {node_id: [alias names, longest first]}."""
        return algorithms.fetch_artist_aliases(self.controller.get, node_ids)

    def _compute_graph_result(self):
        """Build a GraphResult from the DB without touching view state; None when there is nothing to graph."""
        nodes, edges = self.extract_global_graph()
        if not nodes:
            return None

        node_ids = [n[0] for n in nodes]
        clean_edges = dedupe_edges(edges, set(node_ids))
        dendrogram = algorithms.assign_louvain_communities(node_ids, clean_edges)
        scores = algorithms.calculate_influence_scores(node_ids, clean_edges)
        return GraphResult(
            node_names=dict(nodes),
            node_aliases=self.fetch_node_aliases(node_ids),
            edges=clean_edges,
            node_mass=degree_mass(node_ids, clean_edges),
            community_levels=algorithms.filter_eligible_levels(dendrogram) or dendrogram[-1:],
            influence_scores=scores.influence_scores,
            page_rank_scores=scores.page_rank_scores,
            combined_scores=scores.combined_scores,
        )

    def _apply_graph_result(self, result):
        """Copy a GraphResult onto the view and pick the active community level (main thread only)."""
        result = result or GraphResult()
        self.node_names = result.node_names
        self.node_aliases = result.node_aliases
        self.edges = result.edges
        self.node_mass = result.node_mass
        self.influence_scores = result.influence_scores
        self.page_rank_scores = result.page_rank_scores
        self.combined_scores = result.combined_scores
        self.community_levels = result.community_levels

        if not self.community_levels:
            self.active_level = None
            self.community_id = {}
            return
        # Keep the user's chosen granularity across refreshes when it still exists.
        if self.active_level is None or self.active_level >= len(self.community_levels):
            self.active_level = len(self.community_levels) - 1
        self.community_id = self.community_levels[self.active_level]
        self._log_top_scores()

    # -----------------------
    # Node sizing
    # -----------------------
    # min/max are paired with get_label_font_size's range so the smallest box
    # still holds a short label without graph.js growing it.
    def get_node_size(self, node_id, min_size=110, max_size=240):
        """Return a node's minimum box width, log-scaled from its influence score."""
        if node_id not in self.influence_scores:
            return 175

        score = max(0, self.influence_scores[node_id])
        # +2 keeps log2 positive and separates scores 0 and 1.
        normalized = math.log2(score + 2) / math.log2(self._SIZE_SCORE_CAP + 2)
        normalized = min(1.0, normalized) ** 0.6
        return min_size + normalized * (max_size - min_size)

    @staticmethod
    def get_label_font_size(size, min_font=8 * _FONT_SCALE, max_font=13 * _FONT_SCALE):
        """Return a label font size that shrinks for small, low-influence nodes."""
        normalized = (size - 25) / (160 - 25)
        normalized = max(0.0, min(1.0, normalized))
        return min_font + normalized * (max_font - min_font)

    # -----------------------
    # Diagnostics
    # -----------------------
    def debug_graph_structure(self):
        """Log node/edge counts and the most-connected artists at DEBUG level."""
        logger.debug(f"Graph has {len(self.node_names)} nodes and {len(self.edges)} edges")
        top = sorted(self.node_mass.items(), key=lambda x: x[1], reverse=True)[:5]
        for node_id, mass in top:
            logger.debug(f"  {self.node_names.get(node_id, f'Artist {node_id}')}: {mass - 1} connections")

    def _log_top_scores(self):
        """Log the top influence and PageRank artists at DEBUG level."""
        for node_id, score in sorted(self.influence_scores.items(), key=lambda x: x[1], reverse=True)[:10]:
            logger.debug(f"  {self.node_names.get(node_id, f'Artist {node_id}')}: {score} total influenced artists")
        for node_id, pr in sorted(self.page_rank_scores.items(), key=lambda x: x[1], reverse=True)[:10]:
            logger.debug(f"  {self.node_names.get(node_id, f'Artist {node_id}')}: PR={pr:.5f}")

    def debug_size_distribution(self):
        """Log node-size statistics at DEBUG level."""
        if not self.influence_scores or not self.node_names:
            return
        sizes = [self.get_node_size(node_id) for node_id in self.node_names]
        logger.debug(f"Size stats: min={min(sizes):.1f}, max={max(sizes):.1f}, avg={sum(sizes) / len(sizes):.1f}")
