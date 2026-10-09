"""Cytoscape element/style/layout building, incremental updates, JS bridge, and theming for InfluenceGraphView."""

import configparser
import json
from typing import ClassVar

from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger


class InfluenceGraphRenderMixin:
    """Cytoscape bridge; the host provides the web view, graph model, and sizing/color helpers."""

    # Edge styling range, interpolated by the source's influence score.
    _EDGE_OPACITY = (0.18, 0.82)
    _EDGE_WIDTH = (0.8, 2.6)
    _EDGE_ARROW = (0.75, 1.15)

    _THEME_BACKGROUND: ClassVar[dict[str, str]] = {"dark_mode": "#0b0c10", "light_mode": "#f5f6fa", "colorful_mode": "#ffffff", "accessibility_mode": "#ffffff"}

    # -----------------------
    # JS bridge
    # -----------------------
    def _on_page_loaded(self, ok):
        """Mark the page ready and flush JS queued before it loaded."""
        self._page_ready = ok
        pending = self._pending_js
        self._pending_js = []
        for code in pending:
            self._web.page().runJavaScript(code)

    def _run_js(self, code):
        """Run JS now, or queue it until the page has loaded."""
        if self._page_ready:
            self._web.page().runJavaScript(code)
        else:
            self._pending_js.append(code)

    def focus_artist_by_name(self, name):
        """Center on and pulse the node whose name matches `name` (case-insensitive); return True if found."""
        name = (name or "").strip().lower()
        if not name:
            return False
        for node_id, node_name in self.node_names.items():
            if node_name.strip().lower() == name:
                self._run_js(f"focusNode({json.dumps(str(node_id))})")
                return True
        return False

    # -----------------------
    # Theming
    # -----------------------
    def _theme_background(self):
        """Return the canvas background color for the current app theme."""
        theme_name = None
        try:
            theme_name = app_config.get_display_theme()
        except configparser.Error as e:
            logger.warning(f"Could not read display theme from config: {e}")
        return self._THEME_BACKGROUND.get(theme_name, self._THEME_BACKGROUND["dark_mode"])

    # -----------------------
    # Cytoscape data/style/layout building
    # -----------------------
    def _cluster_element(self, community_index):
        """Return the Cytoscape compound-node element for one community."""
        return {"data": {"id": f"c{community_index}", "label": self.community_names.get(community_index, ""), "color": self.get_community_color(community_index).name()}}

    def _node_element(self, node_id, name):
        """Return the Cytoscape element for one artist node."""
        size = self.get_node_size(node_id)
        color = self.get_community_color(self.community_id.get(node_id, 0))
        return {
            "data": {
                "id": str(node_id),
                "label": name,
                "fullLabel": name,
                "aliases": self.node_aliases.get(node_id, []),
                "parent": f"c{self.community_id.get(node_id, 0)}",
                "minWidth": size,
                "minHeight": size * 0.5,
                "fontSize": self.get_label_font_size(size),
                "color": color.name(),
                # Gradient stops must come from one data field; Cytoscape can't join two.
                "gradientColors": f"{color.lighter(130).name()} {color.name()}",
                "borderColor": color.darker(140).name(),
            }
        }

    def _edge_element(self, source_id, target_id, strength):
        """Return the Cytoscape element for one edge; `strength` in [0, 1] drives opacity/width/arrow size."""

        def lerp(bounds):
            low, high = bounds
            return low + strength * (high - low)

        source_color = self.get_community_color(self.community_id.get(source_id, 0))
        arrow_color = self.get_community_color(self.community_id.get(target_id, 0))
        return {
            "data": {
                "id": f"e{source_id}_{target_id}",
                "source": str(source_id),
                "target": str(target_id),
                "opacity": lerp(self._EDGE_OPACITY),
                "width": lerp(self._EDGE_WIDTH),
                "arrowScale": lerp(self._EDGE_ARROW),
                "arrowColor": arrow_color.name(),
                "edgeGradientColors": f"{source_color.name()} {arrow_color.name()}",
            }
        }

    def _build_elements(self):
        """Return every cluster, node, and edge element for the current graph model."""
        elements = []
        seen_clusters = set()
        for node_id, name in self.node_names.items():
            community_index = self.community_id.get(node_id, 0)
            if community_index not in seen_clusters:
                seen_clusters.add(community_index)
                elements.append(self._cluster_element(community_index))
            elements.append(self._node_element(node_id, name))

        # sqrt eases the curve so mid-range influencers stay visible.
        max_score = max(self.influence_scores.values()) if self.influence_scores else 0
        for source_id, target_id in self.edges:
            if source_id not in self.node_names or target_id not in self.node_names:
                continue
            strength = (self.influence_scores.get(source_id, 0) / max_score) ** 0.5 if max_score else 0.0
            elements.append(self._edge_element(source_id, target_id, strength))
        return elements

    def _build_stylesheet(self, bg):
        """Return the Cytoscape stylesheet for clusters, artist nodes, and edges."""
        return [
            {
                "selector": "node:parent",
                "style": {
                    # Soft tinted card behind each community so the grouping reads at a glance.
                    "shape": "round-rectangle",
                    "corner-radius": 22,
                    "background-color": "data(color)",
                    "background-opacity": 0.08,
                    "border-width": 1.4,
                    "border-color": "data(color)",
                    "border-opacity": 0.32,
                    "padding": 32,
                    "label": "data(label)",
                    "color": "data(color)",
                    "font-size": 12,
                    "font-weight": 700,
                    "text-valign": "top",
                    "text-halign": "center",
                    "text-margin-y": -8,
                    "text-background-color": bg,
                    "text-background-opacity": 0.85,
                    "text-background-shape": "round-rectangle",
                    "text-background-padding": 4,
                    # Click-through, or a drag meant to pan would land on the region.
                    "events": "no",
                },
            },
            {
                "selector": "node[parent]",
                "style": {
                    # 'auto' radius gives a full pill, matching the app's chips.
                    "shape": "round-rectangle",
                    "corner-radius": "auto",
                    # Box fits its own wrapped label; graph.js fitNodeLabel floors it at minWidth/minHeight.
                    "width": "label",
                    "height": "label",
                    "padding": 10,
                    "background-fill": "linear-gradient",
                    "background-gradient-direction": "to-bottom-right",
                    "background-gradient-stop-colors": "data(gradientColors)",
                    "background-blacken": -0.04,
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
                    # Wrap at whitespace (never elide) to the node's influence-based width.
                    "text-wrap": "wrap",
                    "text-max-width": "data(minWidth)",
                    # Faint halo keeps dark text legible on darker palette colors.
                    "text-outline-width": 0.6,
                    "text-outline-color": "#ffffff",
                    "text-outline-opacity": 0.25,
                    # Glow substitute (no shadow-* support); eased in on hover/find.
                    "underlay-color": "data(color)",
                    "underlay-opacity": 0,
                    "underlay-padding": 0,
                    "underlay-shape": "round-rectangle",
                    "transition-property": ("underlay-opacity, underlay-padding, border-width, border-opacity"),
                    "transition-duration": 120,
                },
            },
            {"selector": "node[parent].hovered", "style": {"underlay-opacity": 0.35, "underlay-padding": 8, "border-width": 1.6, "border-opacity": 0.9, "z-index": 10}},
            {
                "selector": "edge",
                "style": {
                    "curve-style": "bezier",
                    "width": "data(width)",
                    "line-cap": "round",
                    "line-fill": "linear-gradient",
                    "line-gradient-stop-colors": "data(edgeGradientColors)",
                    "target-arrow-color": "data(arrowColor)",
                    "target-arrow-shape": "triangle-backcurve",
                    "arrow-scale": "data(arrowScale)",
                    "opacity": "data(opacity)",
                },
            },
        ]

    def _build_layout_options(self):
        """Return fcose layout options; graph.js resolveOverlaps removes any overlap fcose leaves."""
        return {
            "name": "fcose",
            "quality": "default",
            # The spectral fallback gives worse first-load layouts than a random scatter.
            "randomize": True,
            "animate": False,
            "nodeDimensionsIncludeLabels": True,
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
        """Send the full graph to Cytoscape, or clear the canvas when the model is empty."""
        if not self.node_names:
            self._run_js("clearGraph()")
            return
        bg = self._theme_background()
        elements = json.dumps(self._build_elements())
        style = json.dumps(self._build_stylesheet(bg))
        layout = json.dumps(self._build_layout_options())
        self._run_js(f"loadGraph({elements}, {style}, {layout}, {json.dumps(bg)})")
        self.debug_graph_structure()

    # -----------------------
    # Incremental live-graph updates
    # -----------------------
    def _new_node_elements(self, artist_id, artist_name):
        """Add an artist to the model and return its elements (plus its cluster if new); [] if present."""
        if artist_id in self.node_names:
            return []
        self.node_names[artist_id] = artist_name
        self.node_aliases[artist_id] = self.fetch_node_aliases([artist_id]).get(artist_id, [])
        self.node_mass[artist_id] = 1
        community_index = self.community_id.get(artist_id, 0)
        cluster_known = any(self.community_id.get(n, 0) == community_index for n in self.node_names if n != artist_id)
        elements = [] if cluster_known else [self._cluster_element(community_index)]
        elements.append(self._node_element(artist_id, artist_name))
        return elements

    def add_single_artist(self, artist_id, artist_name):
        """Add one artist node to the live graph, or refresh its label if it is already there."""
        if artist_id in self.node_names:
            self.node_names[artist_id] = artist_name
            self._run_js(f"setLabel({json.dumps(str(artist_id))}, {json.dumps(artist_name)})")
        else:
            self._run_js(f"addElements({json.dumps(self._new_node_elements(artist_id, artist_name))})")
        self.graph_updated.emit()

    def add_edge(self, source_id, target_id):
        """Add one influence edge between two nodes already in the live graph."""
        if (source_id, target_id) in self.edges or source_id not in self.node_names or target_id not in self.node_names:
            return
        self._run_js(f"addElements({json.dumps([self._edge_model_element(source_id, target_id)])})")

    def _edge_model_element(self, source_id, target_id):
        """Record a new edge in the model and return its element at mid strength."""
        self.edges.append((source_id, target_id))
        self.node_mass[source_id] = self.node_mass.get(source_id, 1) + 1
        self.node_mass[target_id] = self.node_mass.get(target_id, 1) + 1
        # Unranked until the next full recompute, so use the mid-range style.
        return self._edge_element(source_id, target_id, 0.5)

    def add_influence(self, influencer, influenced):
        """Add an (id, name) -> (id, name) relationship to the live graph, adding missing endpoints."""
        if not self.node_names:
            # Nothing on the canvas yet, so build the graph from scratch.
            self.display_global_network()
            return
        (source_id, source_name), (target_id, target_name) = influencer, influenced
        elements = self._new_node_elements(source_id, source_name) + self._new_node_elements(target_id, target_name)
        if (source_id, target_id) not in self.edges:
            elements.append(self._edge_model_element(source_id, target_id))
        if elements:
            self._run_js(f"addElements({json.dumps(elements)})")
        self.graph_updated.emit()
