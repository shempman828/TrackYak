"""
influence_graph_worker.py

Background-worker orchestration for InfluenceGraphView.display_global_network:
runs extraction/scoring off the UI thread, then hands the result back to
the main thread to push into Cytoscape and update the legend.
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class _GlobalGraphWorker(CancellableWorker):
    """Background worker for InfluenceGraphView.display_global_network.

    Extraction and scoring (DB queries + networkx computation) only ever
    touch plain data on the view -- self.controller, self.edges,
    self.node_mass, etc -- never Qt widgets, so it's safe to run them here.
    The Cytoscape push and legend update stay on the main thread, wired up
    through the `finished`/`error` signals, mirroring
    library.duplicate_finder.DuplicateScanWorker.
    """

    finished = Signal(bool)  # True if a graph was computed, False if empty
    error = Signal(str)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view

    def run(self):
        try:
            has_graph = self._view._compute_global_graph()
            self.finished.emit(has_graph)
        except Exception as e:
            # Intentional broad boundary catch: this runs on a QThread and must
            # not let an exception kill the thread silently — surface it to the UI.
            logger.error(f"Error computing influence graph: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            # Extraction is read-only (DB queries via self._view.controller),
            # so nothing else on this thread ever commits/closes -- see
            # CancellableWorker's docstring.
            self._release_db_session()


class InfluenceGraphWorkerMixin:
    """
    Expects the host class to provide: self.node_names, self.edges,
    self.node_mass, self.community_id, self.community_names,
    self.community_levels, self.active_level, self.community_names_by_level,
    self.influence_scores, self.extract_global_graph(),
    self._update_node_mass(), self.assign_louvain_communities(),
    self.calculate_influence_scores(), self._resolve_community_names(),
    self._update_legend(), self._push_graph(), self.debug_size_distribution(),
    and to be a QWidget subclass.
    """

    def display_global_network(self):
        """Kick off a background extraction/scoring pass for the whole
        influence graph. Returns immediately; the Cytoscape push happens
        asynchronously once `_GlobalGraphWorker` reports back via
        `_on_global_graph_computed`/`_on_global_graph_error`.

        A large influence graph makes this the most expensive operation in
        the tab (DB extraction + Louvain + descendant/PageRank scoring), so
        it runs off the UI thread rather than freezing the app on every
        refresh.
        """
        if self._graph_worker is not None and self._graph_worker.isRunning():
            return

        self.node_names = {}
        self.edges = []
        self.node_mass = {}
        self.community_id = {}
        self.community_names = {}
        self.influence_scores = {}
        # community_levels/active_level/community_names_by_level are NOT
        # reset here: assign_louvain_communities() re-derives community_levels
        # fresh every recompute anyway, and preserving active_level across a
        # recompute is what keeps the user's chosen granularity from
        # snapping back to default on every "Refresh".

        self._graph_worker = _GlobalGraphWorker(self)
        self._graph_worker.finished.connect(self._on_global_graph_computed)
        self._graph_worker.error.connect(self._on_global_graph_error)
        self._graph_worker.start()

    def _compute_global_graph(self):
        """Runs on the worker thread. Populates node_names/edges/node_mass/
        community_id/influence_scores; returns False if there was nothing
        to graph."""
        nodes, edges = self.extract_global_graph()

        if not nodes:
            return False

        node_ids = [n[0] for n in nodes]
        node_id_set = set(node_ids)
        self.node_names = {node_id: name for node_id, name in nodes}

        deduped_edges = []
        seen = set()
        for a, b in edges:
            if a in node_id_set and b in node_id_set:
                key = (a, b)
                if key not in seen:
                    seen.add(key)
                    deduped_edges.append(key)
        self.edges = deduped_edges

        self._update_node_mass(node_ids)
        self.assign_louvain_communities(node_ids, self.edges)
        self.calculate_influence_scores(node_ids, self.edges)
        return True

    def _on_global_graph_computed(self, has_graph):
        """Main-thread slot: the only part of this pipeline allowed to
        touch Qt widgets (legend, Cytoscape push)."""
        self._graph_worker = None
        if not has_graph:
            show_status_message(
                self,
                "No artists with influence relationships found. Add some influence relationships first.",
            )
            return
        self._resolve_community_names()
        self._update_legend()
        self._push_graph()
        self.debug_size_distribution()

    def _on_global_graph_error(self, message):
        self._graph_worker = None
        show_status_message(self, f"Failed to build influence graph: {message}")
