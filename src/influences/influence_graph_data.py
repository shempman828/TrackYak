"""
influence_graph_data.py

Pure data logic for InfluenceGraphView: DB extraction of artists/influence
relationships, Louvain community assignment, and influence scoring
(descendant counts + decayed PageRank). No Qt/JS dependency.
"""

import math
from collections import Counter

import networkx as nx
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger


class InfluenceGraphDataMixin:
    """
    Expects the host class to provide: self.controller, self.node_names,
    self.edges, self.node_mass, self.community_id, self.influence_scores,
    and to be a QWidget subclass.
    """

    # -----------------------
    # Graph extraction
    # -----------------------
    def extract_subgraph(self, center_artist_id, degrees):
        """Extract artists and relationships within n degrees of center artist"""
        try:
            visited = {center_artist_id}
            edges = []  # (influencer_id, influenced_id)

            # Expand one BFS level at a time, batching the two lookups
            # ("who influenced this frontier" / "who did this frontier
            # influence") into a single query per direction per level
            # instead of two queries per individual artist. For a
            # `degrees`-hop search this bounds the DB round trips at
            # 2*degrees total, regardless of how many artists are visited.
            frontier = [center_artist_id]
            for _ in range(degrees):
                if not frontier:
                    break

                influences = self.controller.get.get_all_entities(
                    "ArtistInfluence", influenced_id__in=frontier
                )
                influenced = self.controller.get.get_all_entities(
                    "ArtistInfluence", influencer_id__in=frontier
                )

                for influence in influences:
                    edges.append((influence.influencer_id, influence.influenced_id))
                for influence in influenced:
                    edges.append((influence.influencer_id, influence.influenced_id))

                next_frontier = []
                for influence in influences:
                    if influence.influencer_id not in visited:
                        visited.add(influence.influencer_id)
                        next_frontier.append(influence.influencer_id)
                for influence in influenced:
                    if influence.influenced_id not in visited:
                        visited.add(influence.influenced_id)
                        next_frontier.append(influence.influenced_id)

                frontier = next_frontier

            # Dedupe edges (frontiers from different levels can rediscover
            # the same relationship from either endpoint).
            edges = list(dict.fromkeys(edges))

            # Fetch all visited artists' names in one query instead of one
            # `get_entity_object` round trip per artist.
            artists = self.controller.get.get_all_entities(
                "Artist", artist_id__in=list(visited)
            )
            artists_by_id = {artist.artist_id: artist for artist in artists}
            nodes = []
            for artist_id in visited:
                artist = artists_by_id.get(artist_id)
                if artist:
                    nodes.append((artist_id, artist.artist_name))
                else:
                    logger.warning(f"Artist {artist_id} not found in database")

            logger.info(f"Extracted subgraph: {len(nodes)} nodes, {len(edges)} edges")
            return nodes, edges

        except SQLAlchemyError as e:
            logger.error(f"Error extracting subgraph: {e}")
            return [], []

    def extract_global_graph(self):
        """Extract only artists with influence relationships"""
        try:
            # Get all influence relationships first
            all_influences = self.controller.get.get_all_entities("ArtistInfluence")
            logger.info(
                f"Found {len(all_influences)} influence relationships in database"
            )

            if not all_influences:
                logger.warning("No influence relationships found in database!")
                return [], []

            # Collect all unique artist IDs involved in influences
            involved_artist_ids = set()
            edges = []

            for influence in all_influences:
                influencer_id = influence.influencer_id
                influenced_id = influence.influenced_id

                involved_artist_ids.add(influencer_id)
                involved_artist_ids.add(influenced_id)
                edges.append((influencer_id, influenced_id))

            logger.info(
                f"Found {len(involved_artist_ids)} artists with influence relationships"
            )

            # Get only the involved artists from the database in one batched
            # query instead of one round trip per artist.
            artists = self.controller.get.get_all_entities(
                "Artist", artist_id__in=list(involved_artist_ids)
            )
            artists_by_id = {artist.artist_id: artist for artist in artists}
            nodes = []
            for artist_id in involved_artist_ids:
                artist = artists_by_id.get(artist_id)
                if artist:
                    nodes.append((artist_id, artist.artist_name))
                else:
                    logger.warning(
                        f"Artist {artist_id} not found in database but has influence relationships"
                    )

            logger.info(f"Extracted {len(nodes)} nodes and {len(edges)} edges")
            return nodes, edges

        except SQLAlchemyError as e:
            logger.error(f"Error extracting global graph: {e}")
            return [], []

    # -----------------------
    # Node bookkeeping
    # -----------------------
    def _update_node_mass(self, node_ids):
        """Recompute each node's degree-based mass (used to pick a stable
        per-community "anchor" artist for persisted cluster names)."""
        # Single O(e) pass over the edge list building degree counts,
        # instead of rescanning the whole edge list once per node (O(n*e)).
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
        """Assign nodes to Louvain communities for clustering."""
        try:
            G = nx.Graph()
            G.add_nodes_from(node_ids)
            for a, b in edges:
                G.add_edge(a, b)
            import community as community_louvain

            partition = community_louvain.best_partition(G)
            self.community_id = partition
        except (TypeError, nx.NetworkXException) as e:
            logger.error(f"Error computing Louvain communities: {e}")
            self.community_id = {nid: 0 for nid in node_ids}

    # -----------------------
    # Utilities
    # -----------------------
    def debug_graph_structure(self):
        """Log summary info about the graph"""
        try:
            logger.info(
                f"Graph has {len(self.node_names)} nodes and {len(self.edges)} edges"
            )

            connection_counts = {nid: 0 for nid in self.node_names.keys()}
            for a, b in self.edges:
                if a in connection_counts:
                    connection_counts[a] += 1
                if b in connection_counts:
                    connection_counts[b] += 1

            sorted_nodes = sorted(
                connection_counts.items(), key=lambda x: x[1], reverse=True
            )
            logger.info("Top connected nodes:")
            for node_id, count in sorted_nodes[:5]:
                node_name = self.node_names.get(node_id, f"Artist {node_id}")
                logger.info(f"  {node_name}: {count} connections")
        except (AttributeError, TypeError) as e:
            logger.error(f"Error in debug_graph_structure: {e}")

    def check_database_relationships(self):
        """Check if there are any influence relationships in the database"""
        try:
            all_influences = self.controller.get.get_all_entities("ArtistInfluence")
            logger.info(
                f"Total ArtistInfluence relationships in database: {len(all_influences)}"
            )

            if len(all_influences) == 0:
                logger.warning("No ArtistInfluence relationships found in database!")
                return False

            for i, influence in enumerate(all_influences[:5]):  # First 5
                influencer = self.controller.get.get_entity(
                    "Artist", influence.influencer_id
                )
                influenced = self.controller.get.get_entity(
                    "Artist", influence.influenced_id
                )
                influencer_name = (
                    influencer.artist_name
                    if influencer
                    else f"Artist {influence.influencer_id}"
                )
                influenced_name = (
                    influenced.artist_name
                    if influenced
                    else f"Artist {influence.influenced_id}"
                )
                logger.info(
                    f"Relationship {i + 1}: {influencer_name} -> {influenced_name}"
                )

            return True

        except SQLAlchemyError as e:
            logger.error(f"Error checking database relationships: {e}")
            return False

    @staticmethod
    def _compute_descendant_counts(G):
        """Number of distinct nodes reachable from each node in G.

        Equivalent to ``{n: len(nx.descendants(G, n)) for n in G}``, but
        without a full traversal per node: condenses G into its
        strongly-connected-component DAG, accumulates each SCC's downstream
        reachable set once in reverse topological order, then shares that
        set across every member node. This turns an O(n * (n + e)) scan
        into a single O(n + e) pass.
        """
        condensation = nx.condensation(G)
        mapping = condensation.graph["mapping"]

        reachable = {}
        for scc_index in reversed(list(nx.topological_sort(condensation))):
            reach = set(condensation.nodes[scc_index]["members"])
            for successor in condensation.successors(scc_index):
                reach |= reachable[successor]
            reachable[scc_index] = reach

        return {
            node_id: len(reachable[mapping[node_id]] - {node_id})
            for node_id in G.nodes()
        }

    def calculate_influence_scores(self, node_ids, edges):
        """Calculate influence scores and merge with decayed PageRank."""
        try:
            # Build directed graph (influencer -> influenced)
            G = nx.DiGraph()
            G.add_nodes_from(node_ids)

            for source_id, target_id in edges:
                G.add_edge(source_id, target_id)

            # -----------------------------
            # 1. Classic descendant-based influence score
            # -----------------------------
            descendant_counts = self._compute_descendant_counts(G)
            self.influence_scores = {
                node_id: descendant_counts.get(node_id, 0) for node_id in node_ids
            }

            # -----------------------------
            # 2. Decayed PageRank influence weighting
            # -----------------------------
            try:
                decayed_pr = self.compute_decayed_pagerank(G)

                # Store for later use (visualization, layout, scoring)
                self.page_rank_scores = decayed_pr

                # Combine metrics, but keep them separate for now.
                # If you later want a unified score, you can blend them here.
                self.combined_scores = {
                    node: (
                        self.influence_scores.get(node, 0),
                        decayed_pr.get(node, 0.0),
                    )
                    for node in node_ids
                }

            except nx.NetworkXException as e:
                logger.error(f"Failed to compute decayed PageRank: {e}")
                self.page_rank_scores = {}
                self.combined_scores = {
                    node: (self.influence_scores.get(node, 0), 0.0) for node in node_ids
                }

            # -----------------------------
            # Logging output
            # -----------------------------
            logger.info(f"Calculated influence scores for {len(node_ids)} nodes")

            top_influential = sorted(
                self.influence_scores.items(), key=lambda x: x[1], reverse=True
            )[:10]

            logger.info("Top influential artists (unique descendants):")
            for node_id, score in top_influential:
                name = self.node_names.get(node_id, f"Artist {node_id}")
                logger.info(f"  {name}: {score} total influenced artists")

            # If useful, log top PageRank too
            if self.page_rank_scores:
                top_pr = sorted(
                    self.page_rank_scores.items(), key=lambda x: x[1], reverse=True
                )[:10]

                logger.info("Top PageRank artists:")
                for node_id, pr in top_pr:
                    name = self.node_names.get(node_id, f"Artist {node_id}")
                    logger.info(f"  {name}: PR={pr:.5f}")

        except nx.NetworkXException as e:
            logger.error(f"Error calculating influence scores: {e}")
            # Fallback: simple out-degree
            self.influence_scores = {}
            for node_id in node_ids:
                direct = sum(1 for a, b in edges if a == node_id)
                self.influence_scores[node_id] = direct

    def get_node_size(self, node_id, min_size=25, max_size=160):
        """Logarithmic scaling optimized for your score range (0-48)"""
        if not self.influence_scores or node_id not in self.influence_scores:
            return 60

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

    @staticmethod
    def get_label_width(size, name, min_width=70):
        """Width available to a node's label, in the same graph units as
        its box `size`. Small/low-influence nodes get a box far too
        narrow for most names -- decoupling the label from the box lets
        short and medium names render in full (the label simply overflows
        the small pill) while still capping to something sane for very
        long names, so the ellipsis only kicks in when it's genuinely
        needed rather than on almost every small node."""
        return max(size, min(min_width + len(name) * 3.2, 260))

    @staticmethod
    def get_label_font_size(size, min_font=8, max_font=13):
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
        try:
            # We use the existing G passed from calculate_influence_scores
            # G is directed: Influencer -> Influenced

            # Reverse the graph so "votes" flow from the Influenced back to the Influencer
            reversed_G = G.reverse(copy=True)

            # Calculate PageRank
            # alpha=0.85 is the standard decay factor (probability of continuing the chain)
            pagerank_scores = nx.pagerank(reversed_G, alpha=alpha)

            return pagerank_scores

        except nx.NetworkXException as e:
            logger.error(f"Error computing PageRank: {e}")
            # Return 0.0 for all nodes on failure
            return {n: 0.0 for n in G.nodes()}
