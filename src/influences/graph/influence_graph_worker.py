"""Background recompute orchestration for InfluenceGraphView.display_global_network."""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


class _GlobalGraphWorker(CancellableWorker):
    """Run InfluenceGraphView._compute_graph_result off the GUI thread."""

    finished = Signal(object)  # GraphResult, or None when there is nothing to graph
    error = Signal(str)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view

    def run(self):
        try:
            self.finished.emit(self._view._compute_graph_result())
        except Exception as e:
            # Intentional broad boundary catch: an exception must not kill the thread silently.
            logger.error(f"Error computing influence graph: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            # Read-only work, so nothing else on this thread commits/closes the session.
            self._release_db_session()


class InfluenceGraphWorkerMixin:
    """Recompute orchestration; the host provides the data, render, and legend mixins."""

    def is_computing(self):
        """Return True while a background recompute runs."""
        return self._graph_worker is not None and self._graph_worker.isRunning()

    def display_global_network(self):
        """Start a background recompute of the whole graph; a call during a recompute only shows a status."""
        if self.is_computing():
            show_status_message(self, "The influence graph is already refreshing.")
            return

        # graph.js drops the scrim on the first layoutstop.
        self._run_js("showLoading()")
        self._set_busy(True)

        self._graph_worker = _GlobalGraphWorker(self)
        self._graph_worker.finished.connect(self._on_global_graph_computed)
        self._graph_worker.error.connect(self._on_global_graph_error)
        self._graph_worker.start()

    def _set_busy(self, busy):
        """Lock level/rename controls during a recompute and tell listeners."""
        self._legend.set_busy(busy)
        self.busy_changed.emit(busy)

    def _on_global_graph_computed(self, result):
        """Apply the result and push it to Cytoscape (main thread only)."""
        self._graph_worker = None
        self._set_busy(False)
        self._apply_graph_result(result)
        if result is None:
            # _push_graph clears the canvas; no layout runs, so drop the scrim here.
            self._push_graph()
            self._run_js("hideLoading()")
            self._update_legend()
            show_status_message(self, "No artists with influence relationships found. Add some influence relationships first.")
            self.graph_updated.emit()
            return
        self._resolve_community_names()
        self._update_legend()
        self._push_graph()
        self.debug_size_distribution()
        self.graph_updated.emit()

    def _on_global_graph_error(self, message):
        self._graph_worker = None
        self._set_busy(False)
        self._run_js("hideLoading()")
        show_status_message(self, f"Failed to build influence graph: {message}")
