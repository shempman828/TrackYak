from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.foundation.config_setup import app_config
from src.influences.influence_graph_data import InfluenceGraphDataMixin
from src.influences.influence_graph_legend import InfluenceGraphLegendMixin
from src.influences.influence_graph_render import InfluenceGraphRenderMixin
from src.influences.influence_graph_worker import InfluenceGraphWorkerMixin
from src.influences.influence_legend import LegendPanel

_WEB_DIR = Path(__file__).resolve().parent / "web"


class InfluenceGraphView(
    InfluenceGraphDataMixin,
    InfluenceGraphWorkerMixin,
    InfluenceGraphRenderMixin,
    InfluenceGraphLegendMixin,
    QWidget,
):
    """
    Influence graph rendered by Cytoscape.js (in an embedded QWebEngineView)
    using its fcose layout: a compound-node-aware force-directed algorithm
    that groups each Louvain community into an (invisible) parent node and
    lays the whole graph out without overlap. This replaces an earlier
    hand-rolled per-tick Python physics simulation (repulsion/attraction/
    collision), which repeatedly fought itself under hand-tuning.

    DB extraction/scoring lives in InfluenceGraphDataMixin
    (influence_graph_data.py). Background-worker orchestration for
    display_global_network lives in InfluenceGraphWorkerMixin
    (influence_graph_worker.py). Cytoscape element/style/layout building,
    the JS bridge, and theming live in InfluenceGraphRenderMixin
    (influence_graph_render.py). Community-color/naming and the legend
    panel live in InfluenceGraphLegendMixin (influence_graph_legend.py).
    This class owns widget setup and composes the other four.
    """

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web = QWebEngineView(self)
        layout.addWidget(self._web)

        self._page_ready = False
        self._pending_js = []
        self._web.loadFinished.connect(self._on_page_loaded)
        self._web.load(QUrl.fromLocalFile(str(_WEB_DIR / "graph_page.html")))

        self._legend = LegendPanel(
            self,
            on_interact=self._reposition_legend,
            on_rename_all=self._open_rename_all_dialog,
            on_level_changed=self._on_level_changed,
        )
        self._legend.raise_()

        # Graph model (pure data -- Cytoscape/fcose owns layout & rendering)
        self.node_names = {}  # node_id -> name
        self.edges = []  # list of (source_id, target_id) tuples, directed
        self.node_mass = {}  # node_id -> mass (degree-based), used to rank representative artists
        self.community_levels = []  # list[dict[node_id, community_index]], finest first
        self.active_level = None  # index into community_levels; None until first compute
        self.community_id = {}  # node_id -> Louvain community, for the active level
        self.community_names = {}  # community_index -> user-given name, for the active level
        self.community_names_by_level = {}  # level -> {community_index: name}, every eligible level
        self.influence_scores = {}  # node_id -> influence_score

        self.legend_enabled = app_config.get_influence_legend_visible()
        self._graph_worker = None

    # -----------------------
    # Interaction
    # -----------------------
    def fit_to_view(self):
        """Zoom/pan so the whole graph is visible at once."""
        self._run_js("fitView()")
