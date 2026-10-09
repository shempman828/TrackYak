from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.foundation.config_setup import app_config
from src.influences.graph.influence_graph_data import InfluenceGraphDataMixin
from src.influences.graph.influence_graph_legend import InfluenceGraphLegendMixin
from src.influences.graph.influence_graph_render import InfluenceGraphRenderMixin
from src.influences.graph.influence_graph_worker import InfluenceGraphWorkerMixin
from src.influences.graph.influence_legend import LegendPanel

_WEB_DIR = Path(__file__).resolve().parent / "web"


class InfluenceGraphView(InfluenceGraphDataMixin, InfluenceGraphWorkerMixin, InfluenceGraphRenderMixin, InfluenceGraphLegendMixin, QWidget):
    """Influence graph widget: Cytoscape.js with fcose in a QWebEngineView, composed from four mixins."""

    # Emitted when node_names changes, so the "Find artist" completer stays in sync.
    graph_updated = Signal()
    # Emitted with True when a background recompute starts and False when it ends.
    busy_changed = Signal(bool)

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

        self._legend = LegendPanel(self, on_interact=self._reposition_legend, on_rename_all=self._open_rename_all_dialog, on_level_changed=self._on_level_changed)
        self._legend.raise_()

        # Graph model (pure data -- Cytoscape/fcose owns layout & rendering)
        self.node_names = {}  # node_id -> name
        self.node_aliases = {}  # node_id -> [real ArtistAlias names, longest first]
        self.edges = []  # list of (source_id, target_id) tuples, directed
        self.node_mass = {}  # node_id -> mass (degree-based), used to rank representative artists
        self.community_levels = []  # list[dict[node_id, community_index]], finest first
        self.active_level = None  # index into community_levels; None until first compute
        self.community_id = {}  # node_id -> Louvain community, for the active level
        self.community_names = {}  # community_index -> user-given name, for the active level
        self.community_names_by_level = {}  # level -> {community_index: name}, every eligible level
        self.influence_scores = {}  # node_id -> influence_score
        self.page_rank_scores = {}  # node_id -> decayed PageRank
        self.combined_scores = {}  # node_id -> (influence_score, PageRank)

        self.legend_enabled = app_config.get_influence_legend_visible()
        self._graph_worker = None

    # -----------------------
    # Interaction
    # -----------------------
    def fit_to_view(self):
        """Zoom/pan so the whole graph is visible at once."""
        self._run_js("fitView()")
