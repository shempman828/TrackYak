"""
influence_graph_render.py

Cytoscape element/style/layout building, incremental live-graph updates,
the JS bridge, and theming for InfluenceGraphView.
"""

import configparser
import json
from typing import ClassVar

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger


class InfluenceGraphRenderMixin:
    """
    Expects the host class to provide: self._web, self._page_ready,
    self._pending_js, self.node_names, self.node_aliases, self.edges,
    self.node_mass, self.community_id, self.community_names,
    self.influence_scores, self.get_node_size(), self.get_label_font_size(),
    self.get_community_color(), self.debug_graph_structure(),
    self.graph_updated (Signal), and to be a QWidget subclass.
    """

    # Canvas background per app theme, so the graph doesn't stay a
    # hardcoded dark rectangle inside a light/colorful/accessibility theme.
    _THEME_BACKGROUND: ClassVar[dict[str, str]] = {"dark_mode": "#0b0c10", "light_mode": "#f5f6fa", "colorful_mode": "#ffffff", "accessibility_mode": "#ffffff"}

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

    def focus_artist_by_name(self, name):
        """Center/zoom on the node whose name matches `name` (case-
        insensitive, exact) and pulse it, for the toolbar's "Find artist"
        field. Returns True if a matching node was found."""
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
        theme_name = None
        try:
            theme_name = app_config.get_display_theme()
        except configparser.Error as e:
            logger.warning(f"Could not read display theme from config: {e}")
        return self._THEME_BACKGROUND.get(theme_name, self._THEME_BACKGROUND["dark_mode"])

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
                cluster_color = self.get_community_color(community_index)
                elements.append({"data": {"id": cluster_id, "label": self.community_names.get(community_index, ""), "color": cluster_color.name()}})
            size = self.get_node_size(node_id)
            color = self.get_community_color(community_index)
            elements.append(
                {
                    "data": {
                        "id": str(node_id),
                        "label": name,
                        "fullLabel": name,
                        "aliases": self.node_aliases.get(node_id, []),
                        "parent": cluster_id,
                        "minWidth": size,
                        "minHeight": size * 0.5,
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

        # Edge opacity/width/arrow-size by source influence score, matching
        # the original visual language: important influencers get
        # prominent edges, weak ones fade out. sqrt eases the curve for
        # large score ranges.
        MIN_OPACITY, MAX_OPACITY = 0.18, 0.82
        MIN_WIDTH, MAX_WIDTH = 0.8, 2.6
        MIN_ARROW, MAX_ARROW = 0.75, 1.15
        max_score = max(self.influence_scores.values()) if self.influence_scores else 0
        for source_id, target_id in self.edges:
            if source_id not in self.node_names or target_id not in self.node_names:
                continue
            src_score = self.influence_scores.get(source_id, 0)
            t = (src_score / max_score) ** 0.5 if max_score else 0.0
            opacity = MIN_OPACITY + t * (MAX_OPACITY - MIN_OPACITY)
            source_color = self.get_community_color(self.community_id.get(source_id, 0))
            arrow_color = self.get_community_color(self.community_id.get(target_id, 0))
            elements.append(
                {
                    "data": {
                        "id": f"e{source_id}_{target_id}",
                        "source": str(source_id),
                        "target": str(target_id),
                        "opacity": opacity,
                        "width": MIN_WIDTH + t * (MAX_WIDTH - MIN_WIDTH),
                        "arrowScale": MIN_ARROW + t * (MAX_ARROW - MIN_ARROW),
                        "arrowColor": arrow_color.name(),
                        # A soft source->target color blend reads as an
                        # actual connection between two specific clusters,
                        # rather than every edge sharing one flat accent
                        # color regardless of which communities it links.
                        "edgeGradientColors": f"{source_color.name()} {arrow_color.name()}",
                    }
                }
            )
        return elements

    def _build_stylesheet(self, bg):
        return [
            {
                "selector": "node:parent",
                "style": {
                    # Cluster regions used to be fully invisible (just a
                    # floating label) -- the color grouping only showed up
                    # once you looked at individual node fills. A soft
                    # translucent card behind each community, tinted with
                    # that community's own color, makes the grouping
                    # readable at a glance instead of implied.
                    "shape": "round-rectangle",
                    "corner-radius": 22,
                    "background-color": "data(color)",
                    "background-opacity": 0.08,
                    "border-width": 1.4,
                    "border-color": "data(color)",
                    "border-opacity": 0.32,
                    "padding": 32,
                    "label": "data(label)",
                    # Matching the label color to its own region (instead
                    # of one fixed accent for every cluster) visually ties
                    # a cluster's name to its swatch and its nodes.
                    "color": "data(color)",
                    "font-size": 12,
                    "font-weight": 700,
                    "text-valign": "top",
                    "text-halign": "center",
                    "text-margin-y": -8,
                    # A small pill behind the label lifts it off of
                    # whatever nodes happen to sit near the region's top
                    # edge, like a tab on a folder.
                    "text-background-color": bg,
                    "text-background-opacity": 0.85,
                    "text-background-shape": "round-rectangle",
                    "text-background-padding": 4,
                    # Compounds are still click-through despite now being
                    # visible -- their bounding box covers most of the
                    # canvas, and without this a click-drag meant to pan
                    # the viewport would land on the region (nodes are
                    # separately locked via autoungrabify) instead of
                    # reaching the background.
                    "events": "no",
                },
            },
            {
                "selector": "node[parent]",
                "style": {
                    # 'auto' makes the corner radius track the node's own
                    # (smaller) dimension, turning the box into a full
                    # stadium/pill -- matching the pill-shaped chips used
                    # throughout the rest of the app (filter chips, type
                    # chips, "Now Playing" metadata pills) instead of the
                    # barely-rounded rectangle this used to be.
                    "shape": "round-rectangle",
                    "corner-radius": "auto",
                    # 'label' auto-sizes the box to exactly contain its own
                    # (word-wrapped, per text-wrap below) label. graph.js's
                    # fitNodeLabel tries the full artist name first; if that
                    # would grow the box past its influence-based minimum
                    # (minWidth/minHeight data, see get_node_size), it swaps
                    # in a shorter initials-style alias instead of letting
                    # the box balloon -- so box size tracks influence, not
                    # name length, for all but genuinely long names. The
                    # full name is always available on hover (fullLabel
                    # data). Floors at minWidth/minHeight either way, so
                    # the box can grow to fit text but never shrinks below
                    # what influence dictates.
                    "width": "label",
                    "height": "label",
                    "padding": 10,
                    # A diagonal gradient plus a faint negative "blacken"
                    # (a slight overall lighten) reads as a glossy, lit
                    # pill instead of the flat top-to-bottom fill this had
                    # before.
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
                    # 'wrap' breaks a label onto additional lines at
                    # whitespace only (never mid-word) instead of eliding
                    # it -- combined with width/height: 'label' above, the
                    # box always grows to fit whatever this produces, so
                    # text can never overflow its own box.
                    "text-wrap": "wrap",
                    # Wrap at the node's own influence-based target width,
                    # not a flat constant -- a low-influence node's (short,
                    # likely-aliased) label wraps into a narrow column
                    # matching its small box; a high-influence node gets a
                    # proportionally wider column, matching its bigger one.
                    "text-max-width": "data(minWidth)",
                    # A faint light halo keeps the dark label legible
                    # across the full 50-color community palette, some of
                    # which sit darker/more saturated than others.
                    "text-outline-width": 0.6,
                    "text-outline-color": "#ffffff",
                    "text-outline-opacity": 0.25,
                    # A soft colored halo behind the node, off by default
                    # and eased in on hover/find below -- the closest
                    # substitute to a drop-shadow glow this Cytoscape
                    # build offers (no shadow-* style support), but reads
                    # the same way at a glance.
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
            # randomize seeds fcose's force solve from a random scatter; it
            # stays on because the spectral/(0,0) fallback gives noticeably
            # worse first-load layouts, not for any visual effect.
            "randomize": True,
            # Snap straight to the computed layout. fcose solves the final
            # node positions instantly; there is no ongoing computation to
            # visualize, so there is no animation.
            "animate": False,
            # Node boxes auto-size to exactly contain their own label (see
            # _build_stylesheet's width/height: 'label'), so this is a
            # no-op in practice now, but keeps fcose's collision footprint
            # correct if that ever changes.
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
        bg = self._theme_background()
        elements = json.dumps(self._build_elements())
        style = json.dumps(self._build_stylesheet(bg))
        layout = json.dumps(self._build_layout_options())
        self._run_js(f"loadGraph({elements}, {style}, {layout}, {json.dumps(bg)})")
        self.debug_graph_structure()

    # -----------------------
    # Incremental live-graph updates
    # -----------------------
    def add_single_artist(self, artist_id, artist_name):
        """Add a single artist to the existing graph only if it has relationships"""
        try:
            # Check if this artist has any influence relationships
            influences_as_influencer = self.controller.get.get_all_entities("ArtistInfluence", influencer_id=artist_id)
            influences_as_influenced = self.controller.get.get_all_entities("ArtistInfluence", influenced_id=artist_id)

            # Only add if the artist has at least one relationship
            if not influences_as_influencer and not influences_as_influenced:
                logger.info(f"Artist {artist_name} ({artist_id}) has no influence relationships, skipping")
                return

            # If this artist is already in the graph, just update the label
            if artist_id in self.node_names:
                self.node_names[artist_id] = artist_name
                self._run_js(f"setLabel({json.dumps(str(artist_id))}, {json.dumps(artist_name)})")
                self.graph_updated.emit()
                return

            self.node_names[artist_id] = artist_name
            self.node_aliases[artist_id] = self.fetch_node_aliases([artist_id]).get(artist_id, [])
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
                        "fullLabel": artist_name,
                        "aliases": self.node_aliases[artist_id],
                        "parent": cluster_id,
                        "minWidth": size,
                        "minHeight": size * 0.5,
                        "fontSize": self.get_label_font_size(size),
                        "color": color.name(),
                        "gradientColors": f"{color.lighter(130).name()} {color.name()}",
                        "borderColor": color.darker(140).name(),
                    }
                }
            ]
            self._run_js(f"addElements({json.dumps(elements)})")
            self.graph_updated.emit()

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

        source_color = self.get_community_color(self.community_id.get(source_id, 0))
        arrow_color = self.get_community_color(self.community_id.get(target_id, 0))
        # No influence score for a brand-new relationship yet -- mid-range
        # opacity/width/arrow-scale, matching _build_elements' t=0.5 point,
        # until the next full recompute ranks it properly.
        elements = [
            {
                "data": {
                    "id": f"e{source_id}_{target_id}",
                    "source": str(source_id),
                    "target": str(target_id),
                    "opacity": 0.5,
                    "width": 1.7,
                    "arrowScale": 0.95,
                    "arrowColor": arrow_color.name(),
                    "edgeGradientColors": f"{source_color.name()} {arrow_color.name()}",
                }
            }
        ]
        self._run_js(f"addElements({json.dumps(elements)})")
