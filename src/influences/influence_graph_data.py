"""
influence_graph_data.py

Data logic for InfluenceGraphView: DB extraction of artists/influence
relationships, Louvain community assignment, and influence scoring
(descendant counts + decayed PageRank). No Qt/JS dependency.

The actual algorithms live in influence_graph_algorithms.py (Qt-free, so
the statistics module can reuse them for library-wide influence/eclecticism
stats without a QWidget context); this mixin is a thin adapter that calls
them and assigns results onto the host view's instance attributes.
"""

import math

from src.core.logger_config import logger
from src.influences import influence_graph_algorithms as algorithms


class InfluenceGraphDataMixin:
    """
    Expects the host class to provide: self.controller, self.node_names,
    self.edges, self.node_mass, self.community_id, self.influence_scores,
    and to be a QWidget subclass.
    """

    # -----------------------
    # Graph extraction
    # -----------------------
    def extract_global_graph(self):
        """Extract only artists with influence relationships"""
        return algorithms.extract_global_influence_graph(self.controller.get)

    # -----------------------
    # Node bookkeeping
    # -----------------------
    def _update_node_mass(self, node_ids):
        """Recompute each node's degree-based mass (used to pick a stable
        per-community "anchor" artist for persisted cluster names)."""
        # Single O(e) pass over the edge list building degree counts,
        # instead of rescanning the whole edge list once per node (O(n*e)).
        from collections import Counter

        degree = Counter()
        for a, b in self.edges:
            degree[a] += 1
            degree[b] += 1
        for node_id in node_ids:
            self.node_mass[node_id] = 1 + degree.get(node_id, 0)
        current_set = set(node_ids)
        for nid in list(self.node_mass.keys()):
            if nid not in current_set:
                self.node_mass.pop(nid, None)
                self.community_id.pop(nid, None)

    def assign_louvain_communities(self, node_ids, edges):
        """Assign nodes to Louvain communities at every eligible dendrogram
        level, then activate one (the previously-selected level if it's
        still in range, otherwise the coarsest eligible level)."""
        dendrogram = algorithms.assign_louvain_communities(node_ids, edges)
        self.community_levels = algorithms.filter_eligible_levels(dendrogram) or dendrogram[-1:]

        if self.active_level is None or self.active_level >= len(self.community_levels):
            self.active_level = len(self.community_levels) - 1

        self.community_id = self.community_levels[self.active_level]

    # -----------------------
    # Utilities
    # -----------------------
    def debug_graph_structure(self):
        """Log summary info about the graph"""
        try:
            logger.info(f"Graph has {len(self.node_names)} nodes and {len(self.edges)} edges")

            connection_counts = dict.fromkeys(self.node_names.keys(), 0)
            for a, b in self.edges:
                if a in connection_counts:
                    connection_counts[a] += 1
                if b in connection_counts:
                    connection_counts[b] += 1

            sorted_nodes = sorted(connection_counts.items(), key=lambda x: x[1], reverse=True)
            logger.info("Top connected nodes:")
            for node_id, count in sorted_nodes[:5]:
                node_name = self.node_names.get(node_id, f"Artist {node_id}")
                logger.info(f"  {node_name}: {count} connections")
        except (AttributeError, TypeError) as e:
            logger.error(f"Error in debug_graph_structure: {e}")

    def calculate_influence_scores(self, node_ids, edges):
        """Calculate influence scores and merge with decayed PageRank."""
        scores = algorithms.calculate_influence_scores(node_ids, edges)
        self.influence_scores = scores.influence_scores
        self.page_rank_scores = scores.page_rank_scores
        self.combined_scores = scores.combined_scores

        top_influential = sorted(self.influence_scores.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]
        logger.info("Top influential artists (unique descendants):")
        for node_id, score in top_influential:
            name = self.node_names.get(node_id, f"Artist {node_id}")
            logger.info(f"  {name}: {score} total influenced artists")

        if self.page_rank_scores:
            top_pr = sorted(self.page_rank_scores.items(), key=lambda x: x[1], reverse=True)[:10]
            logger.info("Top PageRank artists:")
            for node_id, pr in top_pr:
                name = self.node_names.get(node_id, f"Artist {node_id}")
                logger.info(f"  {name}: PR={pr:.5f}")

    # min_size/max_size are calibrated against get_label_font_size's range
    # (28-45.5 graph units, via _FONT_SCALE) so even the smallest,
    # least-influential node's box can hold a short label -- e.g. an
    # initials-style alias like "C. Aguilera" -- in one or two wrapped
    # lines without graph.js's fitNodeLabel needing to grow it past this
    # influence-driven size for anything but a genuinely long name. The
    # box's pre-_FONT_SCALE dimensions (25-160) were never recalibrated
    # when font size was scaled up 3.5x for legibility, so the smallest
    # box (25 wide) was narrower than a single character at the new font
    # size -- every node's box needed to grow just to hold ANY text, and
    # low-influence nodes with long names grew the most, making node size
    # track name length more than influence (confirmed empirically
    # against the real DB, see scratch/graph_repro/repro.py).
    def get_node_size(self, node_id, min_size=110, max_size=240):
        """Logarithmic scaling optimized for your score range (0-48)"""
        if not self.influence_scores or node_id not in self.influence_scores:
            return 175

        score = self.influence_scores[node_id]

        # Use log base 2 to create more distinction in lower ranges
        # Add 2 to handle score=0 and score=1 gracefully
        log_score = math.log2(score + 2)

        # Normalize based on maximum possible log score (log2(48+2) ≈ 5.64)
        max_log_score = math.log2(50)  # ~5.64
        normalized = log_score / max_log_score

        # Apply additional power scaling
        normalized = normalized**0.6

        size = min_size + normalized * (max_size - min_size)
        return size

    # Cytoscape draws labels in graph space, not screen space, so a label's
    # on-screen size is font-size-in-graph-units * current zoom -- there is
    # no zoom-independent/HUD label mode. The global network can be ~600
    # nodes across ~30 Louvain communities, so cy.fit() (used both by the
    # initial layout and the "Fit to View" button) settles on a small zoom
    # just to fit everyone at once; the previous 8-13 unit font range was
    # sized as if that fit-zoom were close to 1, so at a real fit-zoom of
    # ~0.09-0.12 labels rendered under 1.5px -- invisible, not just small
    # (confirmed empirically against the real DB, see scratch/graph_repro/
    # repro.py). This constant scales font size up so text is legible at a
    # *moderate* zoom-in from that overview (e.g. 3x), where a
    # multi-community cross-section already fits in the viewport.
    #
    # get_node_size's box is no longer the label's rendering box -- it's
    # only a *minimum*. graph.js sizes each node's actual box to exactly
    # contain its own word-wrapped label (Cytoscape's width/height:
    # 'label'), floored at this minimum so low-influence/short-name nodes
    # keep the size-encodes-influence visual language. Two earlier
    # approaches both failed empirically against the real DB (see
    # scratch/graph_repro/repro.py): scaling a separate label-width value
    # in lockstep with font size let overflow reach far enough to be
    # painted over by neighboring nodes' opaque boxes; scaling it down
    # less aggressively still left every label wider than its own box.
    # Auto-sizing the box to its content is the only way to guarantee zero
    # overflow at a legible font size without also inflating the box for
    # every short-named node.
    _FONT_SCALE = 3.5

    @staticmethod
    def get_label_font_size(size, min_font=8 * _FONT_SCALE, max_font=13 * _FONT_SCALE):
        """Scale label font size down for small/low-influence nodes so
        more characters fit before eliding, instead of a fixed size that
        barely shows 2-3 letters on the smallest pills."""
        normalized = (size - 25) / (160 - 25)
        normalized = max(0.0, min(1.0, normalized))
        return min_font + normalized * (max_font - min_font)

    def debug_size_distribution(self):
        """Log the size distribution for analysis"""
        if not self.influence_scores:
            return

        sizes = []
        for node_id in self.node_names.keys():
            size = self.get_node_size(node_id)
            sizes.append((node_id, size, self.influence_scores.get(node_id, 0)))

        sizes.sort(key=lambda x: x[2], reverse=True)

        logger.info("Top 10 node sizes by influence score:")
        for node_id, size, score in sizes[:10]:
            name = self.node_names.get(node_id, f"Artist {node_id}")
            logger.info(f"  {name}: score={score}, size={size:.1f}")

        # Log size statistics
        size_values = [s[1] for s in sizes]
        logger.info(
            f"Size stats: min={min(size_values):.1f}, max={max(size_values):.1f}, avg={sum(size_values) / len(size_values):.1f}"
        )

    def compute_decayed_pagerank(self, G, alpha=0.85):
        """
        Computes PageRank on the reversed graph.

        In the standard graph G (Influencer -> Influenced):
        - A -> B means A influenced B.

        Standard PageRank rewards the *recipient* of the edge (B).
        To reward the *source* (A), we calculate PageRank on G.reverse() (B -> A).

        This treats every person an Artist influenced as a 'vote' for that Artist.
        """
        return algorithms.compute_decayed_pagerank(G, alpha=alpha)
