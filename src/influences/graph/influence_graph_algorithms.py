"""Qt-free influence-graph algorithms, shared by the graph tab and the statistics module."""

from collections import Counter
from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


def extract_global_influence_graph(get_helper):
    """Return (nodes, edges) for every artist in an influence relationship, via a controller.get-like helper."""
    try:
        all_influences = get_helper.get_all_entities("ArtistInfluence")
        logger.info(f"Found {len(all_influences)} influence relationships in database")

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

        logger.info(f"Found {len(involved_artist_ids)} artists with influence relationships")

        artists = get_helper.get_all_entities("Artist", artist_id__in=list(involved_artist_ids))
        artists_by_id = {artist.artist_id: artist for artist in artists}
        nodes = []
        for artist_id in involved_artist_ids:
            artist = artists_by_id.get(artist_id)
            if artist:
                nodes.append((artist_id, artist.artist_name))
            else:
                logger.warning(f"Artist {artist_id} not found in database but has influence relationships")

        logger.info(f"Extracted {len(nodes)} nodes and {len(edges)} edges")
        return nodes, edges

    except SQLAlchemyError as e:
        logger.error(f"Error extracting global graph: {e}")
        return [], []


def fetch_artist_aliases(get_helper, artist_ids):
    """Return {artist_id: [ArtistAlias names, longest first]} for graph.js's label shortening."""
    if not artist_ids:
        return {}
    try:
        aliases = get_helper.get_all_entities("ArtistAlias", artist_id__in=list(artist_ids))
    except SQLAlchemyError as e:
        logger.error(f"Error fetching artist aliases: {e}")
        return {}

    by_artist = {}
    for alias in aliases:
        by_artist.setdefault(alias.artist_id, []).append(alias.alias_name)
    for names in by_artist.values():
        names.sort(key=len, reverse=True)
    return by_artist


def compute_descendant_counts(G):
    """Return the number of distinct nodes reachable from each node in G."""
    # One pass over the SCC condensation DAG instead of nx.descendants() per node.
    condensation = nx.condensation(G)
    mapping = condensation.graph["mapping"]

    reachable = {}
    for scc_index in reversed(list(nx.topological_sort(condensation))):
        reach = set(condensation.nodes[scc_index]["members"])
        for successor in condensation.successors(scc_index):
            reach |= reachable[successor]
        reachable[scc_index] = reach

    return {node_id: len(reachable[mapping[node_id]] - {node_id}) for node_id in G.nodes()}


def compute_decayed_pagerank(G, alpha=0.85):
    """Return PageRank of the reversed graph, so each influenced artist is a vote for its influencer."""
    try:
        reversed_G = G.reverse(copy=True)
        return nx.pagerank(reversed_G, alpha=alpha)
    except nx.NetworkXException as e:
        logger.error(f"Error computing PageRank: {e}")
        return dict.fromkeys(G.nodes(), 0.0)


@dataclass
class InfluenceScores:
    """Descendant-count, PageRank, and combined (count, PageRank) scores per node."""

    influence_scores: dict = field(default_factory=dict)
    page_rank_scores: dict = field(default_factory=dict)
    combined_scores: dict = field(default_factory=dict)


def calculate_influence_scores(node_ids, edges):
    """Return InfluenceScores (descendant counts and decayed PageRank) for the given graph."""
    try:
        G = nx.DiGraph()
        G.add_nodes_from(node_ids)
        G.add_edges_from(edges)

        descendant_counts = compute_descendant_counts(G)
        influence_scores = {node_id: descendant_counts.get(node_id, 0) for node_id in node_ids}

        # compute_decayed_pagerank catches its own NetworkXException.
        page_rank_scores = compute_decayed_pagerank(G)
        combined_scores = {node: (influence_scores.get(node, 0), page_rank_scores.get(node, 0.0)) for node in node_ids}

        logger.debug(f"Calculated influence scores for {len(node_ids)} nodes")
        return InfluenceScores(influence_scores=influence_scores, page_rank_scores=page_rank_scores, combined_scores=combined_scores)

    except nx.NetworkXException as e:
        logger.error(f"Error calculating influence scores: {e}")
        # Fallback: simple out-degree.
        out_degree = Counter(a for a, _b in edges)
        influence_scores = {node_id: out_degree.get(node_id, 0) for node_id in node_ids}
        return InfluenceScores(influence_scores=influence_scores)


# Fixed seed so the same graph gives the same communities (and colors) on every refresh.
LOUVAIN_RANDOM_STATE = 0


def assign_louvain_communities(node_ids, edges):
    """Return Louvain partitions for every dendrogram level, finest first; one flat community on failure."""
    try:
        G = nx.Graph()
        G.add_nodes_from(node_ids)
        G.add_edges_from(edges)
        import community as community_louvain

        dendrogram = community_louvain.generate_dendrogram(G, random_state=LOUVAIN_RANDOM_STATE)
        return [community_louvain.partition_at_level(dendrogram, level) for level in range(len(dendrogram))]
    except (TypeError, nx.NetworkXException) as e:
        logger.error(f"Error computing Louvain communities: {e}")
        return [dict.fromkeys(node_ids, 0)]


def filter_eligible_levels(dendrogram, max_dominant_fraction=0.8):
    """Return the dendrogram levels worth showing: 2+ communities, no dominant blob, no repeated grouping."""
    eligible = []
    prev_signature = None
    total_nodes = None
    for partition in dendrogram:
        if total_nodes is None:
            total_nodes = len(partition)
        if not total_nodes:
            continue

        members_by_community = {}
        for node_id, community_index in partition.items():
            members_by_community.setdefault(community_index, set()).add(node_id)

        if len(members_by_community) < 2:
            continue
        if max(len(m) for m in members_by_community.values()) > (max_dominant_fraction * total_nodes):
            continue

        signature = frozenset(frozenset(m) for m in members_by_community.values())
        if signature == prev_signature:
            continue

        eligible.append(partition)
        prev_signature = signature

    return eligible


def compute_community_bridge_counts(node_ids, edges, community_id):
    """Return per-artist count of distinct communities among direct neighbors (the "eclecticism" metric)."""
    neighbors = {node_id: set() for node_id in node_ids}
    for a, b in edges:
        if a in neighbors:
            neighbors[a].add(b)
        if b in neighbors:
            neighbors[b].add(a)

    return {node_id: len({community_id[n] for n in neighbor_set if n in community_id}) for node_id, neighbor_set in neighbors.items()}
