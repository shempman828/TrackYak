"""
influence_stats_worker.py

InfluenceStatsWorker: computes library-wide "most influential artist" and
"most eclectic artist" off the GUI thread, reusing the same graph
algorithms as the Influence Graph tab (src/influences/influence_graph_algorithms.py)
rather than inventing new formulas. Unlike the graph tab, this runs over
every artist with an influence relationship, not just a displayed subgraph.

Takes controller.get (a GetFromDB instance) rather than a session_factory,
since it goes through the same DB-access helper the influence graph view
uses (get_all_entities), not raw SQLAlchemy sessions.
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger
from src.influences import influence_graph_algorithms as algorithms


class InfluenceStatsWorker(CancellableWorker):
    """Runs the influence/eclecticism computation off the main thread.

    Emits a plain dict — no ORM objects crossing the thread boundary,
    matching the existing StatisticsWorker convention.
    """

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, get_helper, parent=None):
        super().__init__(parent)
        self.get_helper = get_helper

    def run(self):
        try:
            nodes, edges = algorithms.extract_global_influence_graph(self.get_helper)
            if not nodes:
                self.finished.emit({"most_influential": None, "most_eclectic": None})
                return

            node_ids = [node_id for node_id, _name in nodes]
            names_by_id = dict(nodes)

            scores = algorithms.calculate_influence_scores(node_ids, edges)
            community_id = algorithms.assign_louvain_communities(node_ids, edges)
            bridge_counts = algorithms.compute_community_bridge_counts(
                node_ids, edges, community_id
            )

            most_influential = None
            if scores.influence_scores:
                top_id, top_score = max(scores.influence_scores.items(), key=lambda item: item[1])
                most_influential = (names_by_id.get(top_id, f"Artist {top_id}"), top_score)

            most_eclectic = None
            if bridge_counts:
                top_id, top_bridges = max(bridge_counts.items(), key=lambda item: item[1])
                most_eclectic = (names_by_id.get(top_id, f"Artist {top_id}"), top_bridges)

            self.finished.emit(
                {"most_influential": most_influential, "most_eclectic": most_eclectic}
            )
        except (RuntimeError, ValueError) as e:
            logger.error(f"Error computing influence statistics: {e}")
            self.error.emit(str(e))
        finally:
            # Read-only, lazy-loaded per dialog session — see
            # CancellableWorker's _release_db_session docstring.
            self._release_db_session()
