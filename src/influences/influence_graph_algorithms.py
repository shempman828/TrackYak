"""
influence_graph_algorithms.py

Pure, Qt-free graph algorithms for the artist influence graph: DB extraction,
Louvain community assignment, descendant-count/PageRank influence scoring,
and community-bridging ("eclecticism") scoring.

Extracted out of InfluenceGraphDataMixin so this logic is callable from
contexts that aren't a QWidget with `self.controller`/`self.node_names`/etc
-- specifically the statistics module's InfluenceStatsWorker, which needs
influence_scores and community bridging for every artist, not just those
displayed in the graph tab.
"""

from collections import Counter
from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger


def extract_global_influence_graph(get_helper):
    """Extract only artists with influence relationships.

    `get_helper` is a GetFromDB-like object (controller.get) exposing
    `get_all_entities(entity_name, **filters)`.
    """
    try:
        all_influences = get_helper.get_all_entities("ArtistInfluence")
        logger.info(
            f"Found {len(all_influences)} influence relationships in database"
        )

        if not all_influences:
            logger.warning("No influence relationships found in database!")
            return [], []

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

        artists = get_helper.get_all_entities(
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


def compute_descendant_counts(G):
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


def compute_decayed_pagerank(G, alpha=0.85):
    """
    Computes PageRank on the reversed graph.

    In the standard graph G (Influencer -> Influenced):
    - A -> B means A influenced B.

    Standard PageRank rewards the *recipient* of the edge (B).
    To reward the *source* (A), we calculate PageRank on G.reverse() (B -> A).

    This treats every person an Artist influenced as a 'vote' for that Artist.
    """
    try:
        reversed_G = G.reverse(copy=True)
        return nx.pagerank(reversed_G, alpha=alpha)
    except nx.NetworkXException as e:
        logger.error(f"Error computing PageRank: {e}")
        return {n: 0.0 for n in G.nodes()}


@dataclass
class InfluenceScores:
    influence_scores: dict = field(default_factory=dict)
    page_rank_scores: dict = field(default_factory=dict)
    combined_scores: dict = field(default_factory=dict)


def calculate_influence_scores(node_ids, edges):
    """Calculate descendant-count influence scores and decayed PageRank.

    Returns an InfluenceScores dataclass rather than mutating instance
    state, so this is safely callable outside a QWidget context.
    """
    try:
        G = nx.DiGraph()
        G.add_nodes_from(node_ids)
        for source_id, target_id in edges:
            G.add_edge(source_id, target_id)

        descendant_counts = compute_descendant_counts(G)
        influence_scores = {
            node_id: descendant_counts.get(node_id, 0) for node_id in node_ids
        }

        try:
            decayed_pr = compute_decayed_pagerank(G)
            page_rank_scores = decayed_pr
            combined_scores = {
                node: (influence_scores.get(node, 0), decayed_pr.get(node, 0.0))
                for node in node_ids
            }
        except nx.NetworkXException as e:
            logger.error(f"Failed to compute decayed PageRank: {e}")
            page_rank_scores = {}
            combined_scores = {
                node: (influence_scores.get(node, 0), 0.0) for node in node_ids
            }

        logger.info(f"Calculated influence scores for {len(node_ids)} nodes")
        return InfluenceScores(
            influence_scores=influence_scores,
            page_rank_scores=page_rank_scores,
            combined_scores=combined_scores,
        )

    except nx.NetworkXException as e:
        logger.error(f"Error calculating influence scores: {e}")
        # Fallback: simple out-degree
        influence_scores = {}
        for node_id in node_ids:
            direct = sum(1 for a, b in edges if a == node_id)
            influence_scores[node_id] = direct
        return InfluenceScores(influence_scores=influence_scores)


def assign_louvain_communities(node_ids, edges):
    """Assign nodes to Louvain communities for clustering.

    Returns dict[node_id, community_index]. Falls back to a single
    community for every node on failure.
    """
    try:
        G = nx.Graph()
        G.add_nodes_from(node_ids)
        for a, b in edges:
            G.add_edge(a, b)
        import community as community_louvain

        return community_louvain.best_partition(G)
    except (TypeError, nx.NetworkXException) as e:
        logger.error(f"Error computing Louvain communities: {e}")
        return {nid: 0 for nid in node_ids}


def compute_community_bridge_counts(node_ids, edges, community_id):
    """Per-artist count of distinct Louvain communities among their direct
    (undirected) neighbors -- the "eclecticism" metric: an artist whose
    connections span many different communities bridges genres/scenes more
    than one whose connections all sit inside their own community.
    """
    neighbors = {node_id: set() for node_id in node_ids}
    for a, b in edges:
        if a in neighbors:
            neighbors[a].add(b)
        if b in neighbors:
            neighbors[b].add(a)

    return {
        node_id: len(
            {community_id[n] for n in neighbor_set if n in community_id}
        )
        for node_id, neighbor_set in neighbors.items()
    }
