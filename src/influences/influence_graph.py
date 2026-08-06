import configparser
import json
import math
from collections import Counter
from pathlib import Path

import networkx as nx
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.config_setup import app_config
from src.influences.cluster_name_dialog import ClusterNamesDialog
from src.influences.community_palette import generate_community_palette
from src.influences.influence_legend import LegendPanel
from src.core.logger_config import logger
from src.core.status_utility import show_status_message

_WEB_DIR = Path(__file__).resolve().parent / "web"


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


class InfluenceGraphView(QWidget):
    """
    Influence graph rendered by Cytoscape.js (in an embedded QWebEngineView)
    using its fcose layout: a compound-node-aware force-directed algorithm
    that groups each Louvain community into an (invisible) parent node and
    lays the whole graph out without overlap. This replaces an earlier
    hand-rolled per-tick Python physics simulation (repulsion/attraction/
    collision), which repeatedly fought itself under hand-tuning.
    """

    # Canvas background per app theme, so the graph doesn't stay a
    # hardcoded dark rectangle inside a light/colorful/accessibility theme.
    _THEME_BACKGROUND = {
        "dark_mode": "#0b0c10",
        "light_mode": "#f5f6fa",
        "colorful_mode": "#ffffff",
        "accessibility_mode": "#ffffff",
    }

    # Generated, not hand-picked: supports up to 50 communities with maximal
    # visual distinction (golden-angle hue spread — see
    # generate_community_palette). Communities beyond 50 wrap around and
    # reuse these hues at alternating lighter/darker strengths.
    _COMMUNITY_PALETTE = generate_community_palette(50)

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
        )
        self._legend.raise_()

        # Graph model (pure data -- Cytoscape/fcose owns layout & rendering)
        self.node_names = {}  # node_id -> name
        self.edges = []  # list of (source_id, target_id) tuples, directed
        self.node_mass = {}  # node_id -> mass (degree-based), used for anchor picking
        self.community_id = {}  # node_id -> Louvain community
        self.community_names = {}  # community_index -> user-given name (this session)
        self._community_anchor = {}  # community_index -> anchor node_id (naming key)
        self.influence_scores = {}  # node_id -> influence_score

        self.legend_enabled = app_config.get_influence_legend_visible()
        self._graph_worker = None

    # -----------------------
    # JS bridge
    # -----------------------
    def _on_page_loaded(self, ok):
        self._page_ready = ok
        pending = self._pending_js
        self._pending_js = []
        for code in pending:
            self._web.page().runJavaScript(code)

    def _run_js(self, code):
        if self._page_ready:
            self._web.page().runJavaScript(code)
        else:
            self._pending_js.append(code)

    # -----------------------
    # Theming
    # -----------------------
    def _theme_background(self):
        theme_name = None
        try:
            theme_name = app_config.get_display_theme()
        except configparser.Error as e:
            logger.warning(f"Could not read display theme from config: {e}")
        return self._THEME_BACKGROUND.get(theme_name, self._THEME_BACKGROUND["dark_mode"])

    def get_community_color(self, community_index):
        """Map a Louvain community id to a stable, visually distinct color.

        Indexes into the generated 50-color palette. Communities beyond the
        base 50 reuse it at alternating lighter/darker strengths rather than
        falling back to arbitrary hues.
        """
        palette = self._COMMUNITY_PALETTE
        lap, idx = divmod(community_index, len(palette))
        color = QColor(palette[idx])
        if lap == 1:
            color = color.lighter(122)
        elif lap >= 2:
            factor = 115 + 12 * (lap - 1)
            color = color.darker(factor) if lap % 2 == 0 else color.lighter(factor)
        return color

    def _resolve_community_names(self):
        """Re-attach persisted cluster names after a Louvain recompute.

        Raw community indices are reassigned every time Louvain runs, so a
        name can't be pinned to an index. Instead each community is pinned to
        its highest-degree "anchor" artist (stable enough across re-runs on
        the same data), and names are persisted keyed by that artist's id.
        """
        anchors = {}
        for node_id, community_index in self.community_id.items():
            mass = self.node_mass.get(node_id, 0)
            current = anchors.get(community_index)
            if current is None or mass > self.node_mass.get(current, 0):
                anchors[community_index] = node_id
        self._community_anchor = anchors

        saved_names = app_config.get_influence_cluster_names()
        self.community_names = {
            community_index: saved_names[str(anchor_id)]
            for community_index, anchor_id in anchors.items()
            if str(anchor_id) in saved_names
        }

    def rename_communities(self, new_names_by_index):
        """Rename any number of clusters at once and persist each against
        its anchor artist (see ClusterNamesDialog, opened from the legend
        panel's "Rename…" button)."""
        saved_names = app_config.get_influence_cluster_names()
        for community_index, name in new_names_by_index.items():
            anchor_id = self._community_anchor.get(community_index)
            if anchor_id is None:
                continue
            name = name.strip()
            if name:
                self.community_names[community_index] = name
                saved_names[str(anchor_id)] = name
            else:
                self.community_names.pop(community_index, None)
                saved_names.pop(str(anchor_id), None)
            self._run_js(
                f"setLabel({json.dumps(f'c{community_index}')}, {json.dumps(name)})"
            )
        app_config.set_influence_cluster_names(saved_names)
        app_config.save()
        self._update_legend()

    def _open_rename_all_dialog(self):
        rows = self._legend_rows()
        if not rows:
            return
        dialog = ClusterNamesDialog(rows, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.rename_communities(dialog.cluster_names())

    def set_legend_visible(self, visible: bool):
        """Show/hide the cluster legend overlay, persisting the preference."""
        self.legend_enabled = visible
        app_config.set_influence_legend_visible(visible)
        app_config.save()
        self._update_legend()

    def _representative_artists(self, members_by_community, community_index, limit=5):
        """Return up to `limit` artist names for a community, ranked by mass
        (the same "most prominent node" weighting used for the anchor pick
        in _resolve_community_names) so the user has enough signal to tell
        clusters apart when renaming them.
        """
        members = sorted(
            members_by_community.get(community_index, []),
            key=lambda node_id: self.node_mass.get(node_id, 0),
            reverse=True,
        )
        return [self.node_names.get(node_id, "") for node_id in members[:limit]]

    def _legend_rows(self):
        counts = {}
        members_by_community = {}
        for node_id, community_index in self.community_id.items():
            counts[community_index] = counts.get(community_index, 0) + 1
            members_by_community.setdefault(community_index, []).append(node_id)
        sized = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            (
                community_index,
                self.get_community_color(community_index),
                count,
                self.community_names.get(community_index, ""),
                self._representative_artists(members_by_community, community_index),
            )
            for community_index, count in sized
        ]

    def _update_legend(self):
        rows = self._legend_rows()
        if self.legend_enabled:
            self._legend.set_communities(rows)
        else:
            self._legend.hide()
        self._reposition_legend()

    def _reposition_legend(self):
        if self._legend.has_custom_position():
            self._legend.clamp_to_parent()
        else:
            margin = 14
            self._legend.move(margin, self.height() - self._legend.height() - margin)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_legend()

    # -----------------------
    # Interaction
    # -----------------------
    def fit_to_view(self):
        """Zoom/pan so the whole graph is visible at once."""
        self._run_js("fitView()")

    # -----------------------
    # Entry point
    # -----------------------
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
        self._community_anchor = {}
        self.influence_scores = {}

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
    # Cytoscape data/style/layout building
    # -----------------------
    def _build_elements(self):
        elements = []
        seen_clusters = set()
        for node_id, name in self.node_names.items():
            community_index = self.community_id.get(node_id, 0)
            cluster_id = f"c{community_index}"
            if cluster_id not in seen_clusters:
                seen_clusters.add(cluster_id)
                elements.append(
                    {
                        "data": {
                            "id": cluster_id,
                            "label": self.community_names.get(community_index, ""),
                        }
                    }
                )
            size = self.get_node_size(node_id)
            color = self.get_community_color(community_index)
            elements.append(
                {
                    "data": {
                        "id": str(node_id),
                        "label": name,
                        "parent": cluster_id,
                        "width": size,
                        "height": size * 0.5,
                        "labelWidth": self.get_label_width(size, name),
                        "fontSize": self.get_label_font_size(size),
                        "color": color.name(),
                        # background-gradient-stop-colors takes its whole
                        # value from a single data field already containing
                        # the space-separated stop colors -- it can't be
                        # built from two separate data() calls in one
                        # property string.
                        "gradientColors": f"{color.lighter(130).name()} {color.name()}",
                        "borderColor": color.darker(140).name(),
                    }
                }
            )

        # Edge opacity by source influence score, matching the original
        # visual language: important influencers get prominent edges, weak
        # ones fade out. sqrt eases the curve for large score ranges.
        MIN_OPACITY, MAX_OPACITY = 0.18, 0.82
        max_score = max(self.influence_scores.values()) if self.influence_scores else 0
        for source_id, target_id in self.edges:
            if source_id not in self.node_names or target_id not in self.node_names:
                continue
            src_score = self.influence_scores.get(source_id, 0)
            t = (src_score / max_score) ** 0.5 if max_score else 0.0
            opacity = MIN_OPACITY + t * (MAX_OPACITY - MIN_OPACITY)
            arrow_color = self.get_community_color(self.community_id.get(target_id, 0))
            elements.append(
                {
                    "data": {
                        "id": f"e{source_id}_{target_id}",
                        "source": str(source_id),
                        "target": str(target_id),
                        "opacity": opacity,
                        "arrowColor": arrow_color.name(),
                    }
                }
            )
        return elements

    def _build_stylesheet(self):
        return [
            {
                "selector": "node:parent",
                "style": {
                    "background-opacity": 0,
                    "border-width": 0,
                    "label": "data(label)",
                    "color": "#8599ea",
                    "font-size": 11,
                    "text-valign": "top",
                    "text-halign": "center",
                    # Compounds are invisible but still hit-testable by
                    # default, and their bounding box covers most of the
                    # canvas -- without this, a click-drag meant to pan
                    # the viewport lands on the compound (nothing visible,
                    # nothing happens, since nodes are separately locked
                    # via autoungrabify) instead of reaching the
                    # background almost everywhere except directly on a
                    # node. This makes compounds click-through.
                    "events": "no",
                },
            },
            {
                "selector": "node[parent]",
                "style": {
                    "shape": "round-rectangle",
                    "corner-radius": 9,
                    "width": "data(width)",
                    "height": "data(height)",
                    # Soft top-to-bottom gradient instead of a flat fill,
                    # closer to the original hand-painted glassy look than
                    # a plain solid rectangle.
                    "background-fill": "linear-gradient",
                    "background-gradient-direction": "to-bottom",
                    "background-gradient-stop-colors": "data(gradientColors)",
                    "border-width": 1,
                    "border-color": "data(borderColor)",
                    "border-opacity": 0.55,
                    "label": "data(label)",
                    "color": "#0b0c10",
                    "font-size": "data(fontSize)",
                    "font-family": "Helvetica Neue, Helvetica, Arial, sans-serif",
                    "font-weight": 600,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "ellipsis",
                    "text-max-width": "data(labelWidth)",
                    # A faint light halo keeps the dark label legible
                    # across the full 50-color community palette, some of
                    # which sit darker/more saturated than others.
                    "text-outline-width": 0.6,
                    "text-outline-color": "#ffffff",
                    "text-outline-opacity": 0.25,
                },
            },
            {
                "selector": "node[parent].hovered",
                "style": {
                    "border-width": 2.5,
                    "border-opacity": 1,
                    "border-color": "#ffffff",
                },
            },
            {
                "selector": "edge",
                "style": {
                    "curve-style": "bezier",
                    "width": 1.1,
                    "line-cap": "round",
                    "line-color": "#8599ea",
                    "target-arrow-color": "data(arrowColor)",
                    "target-arrow-shape": "triangle-backcurve",
                    "arrow-scale": 1.0,
                    "opacity": "data(opacity)",
                },
            },
        ]

    def _build_layout_options(self):
        # fcose (Fast Compound Spring Embedder) -- a proven force-directed
        # layout with first-class support for compound nodes (our
        # per-community groups): it pulls same-parent nodes together and
        # pushes separate compounds apart. NOTE: fcose is a force-directed
        # heuristic, not a hard collision constraint solver -- it settles
        # at an energy equilibrium that usually keeps nodes apart but can
        # still leave pairs overlapping, especially inside a densely
        # packed community. The actual overlap-free guarantee comes from
        # a deterministic separation pass (resolveOverlaps in graph.js)
        # that runs after every layout settles. Replaces the earlier
        # hand-rolled repulsion/cohesion/collision system. All values here
        # are tunable knobs if the grouping still needs to feel
        # tighter/looser.
        return {
            "name": "fcose",
            "quality": "default",
            "randomize": True,
            "animate": True,
            # Labels can render wider than a node's own box (small/
            # low-influence pills let their name overflow rather than
            # ellipsize -- see get_label_width). Without this, fcose only
            # keeps the boxes themselves from overlapping and two nearby
            # nodes' overflowing labels can still visually collide; this
            # makes the layout treat each node's true on-screen footprint
            # (box + label) as its collision size.
            "nodeDimensionsIncludeLabels": True,
            # fcose computes the final layout instantly, then tweens nodes
            # from their random starting scatter into place over this
            # duration -- stretched well past the library default (1000ms)
            # so the resolve is actually enjoyable to watch, the way the
            # old per-tick simulation was, without the computation itself
            # being slow.
            "animationDuration": 2200,
            "animationEasing": "ease-out",
            "fit": True,
            "padding": 40,
            "nodeRepulsion": 9000,
            "idealEdgeLength": 90,
            "edgeElasticity": 0.45,
            "nestingFactor": 0.12,
            "gravity": 0.3,
            "gravityRange": 3.8,
            "gravityCompound": 1.2,
            "gravityRangeCompound": 1.8,
            "numIter": 2500,
            "tile": True,
            "tilingPaddingVertical": 20,
            "tilingPaddingHorizontal": 20,
        }

    def _push_graph(self):
        elements = json.dumps(self._build_elements())
        style = json.dumps(self._build_stylesheet())
        layout = json.dumps(self._build_layout_options())
        bg = json.dumps(self._theme_background())
        self._run_js(f"loadGraph({elements}, {style}, {layout}, {bg})")
        self.debug_graph_structure()

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

    def add_single_artist(self, artist_id, artist_name):
        """Add a single artist to the existing graph only if it has relationships"""
        try:
            # Check if this artist has any influence relationships
            influences_as_influencer = self.controller.get.get_all_entities(
                "ArtistInfluence", influencer_id=artist_id
            )
            influences_as_influenced = self.controller.get.get_all_entities(
                "ArtistInfluence", influenced_id=artist_id
            )

            # Only add if the artist has at least one relationship
            if not influences_as_influencer and not influences_as_influenced:
                logger.info(
                    f"Artist {artist_name} ({artist_id}) has no influence relationships, skipping"
                )
                return

            # If this artist is already in the graph, just update the label
            if artist_id in self.node_names:
                self.node_names[artist_id] = artist_name
                self._run_js(
                    f"setLabel({json.dumps(str(artist_id))}, {json.dumps(artist_name)})"
                )
                return

            self.node_names[artist_id] = artist_name
            self.node_mass[artist_id] = 1
            community_index = self.community_id.get(artist_id, 0)
            cluster_id = f"c{community_index}"
            size = self.get_node_size(artist_id)
            color = self.get_community_color(community_index)

            elements = [
                {
                    "data": {
                        "id": str(artist_id),
                        "label": artist_name,
                        "parent": cluster_id,
                        "width": size,
                        "height": size * 0.5,
                        "labelWidth": self.get_label_width(size, artist_name),
                        "fontSize": self.get_label_font_size(size),
                        "color": color.name(),
                        "gradientColors": f"{color.lighter(130).name()} {color.name()}",
                        "borderColor": color.darker(140).name(),
                    }
                }
            ]
            self._run_js(f"addElements({json.dumps(elements)})")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error adding single artist {artist_id}: {e}")

    def add_edge(self, source_id, target_id):
        """Add one influence edge to the live graph model + canvas, without
        a full reload."""
        key = (source_id, target_id)
        if key in self.edges:
            return
        if source_id not in self.node_names or target_id not in self.node_names:
            return

        self.edges.append(key)
        self.node_mass[source_id] = self.node_mass.get(source_id, 1) + 1
        self.node_mass[target_id] = self.node_mass.get(target_id, 1) + 1

        arrow_color = self.get_community_color(self.community_id.get(target_id, 0))
        elements = [
            {
                "data": {
                    "id": f"e{source_id}_{target_id}",
                    "source": str(source_id),
                    "target": str(target_id),
                    "opacity": 0.5,
                    "arrowColor": arrow_color.name(),
                }
            }
        ]
        self._run_js(f"addElements({json.dumps(elements)})")

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
