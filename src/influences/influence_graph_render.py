"""
influence_graph_render.py

Cytoscape element/style/layout building, incremental live-graph updates,
the JS bridge, and theming for InfluenceGraphView.
"""

import configparser
import json

from sqlalchemy.exc import SQLAlchemyError

from src.core.config_setup import app_config
from src.core.logger_config import logger


class InfluenceGraphRenderMixin:
    """
    Expects the host class to provide: self._web, self._page_ready,
    self._pending_js, self.node_names, self.edges, self.node_mass,
    self.community_id, self.community_names, self.influence_scores,
    self.get_node_size(), self.get_label_width(), self.get_label_font_size(),
    self.get_community_color(), self.debug_graph_structure(), and to be a
    QWidget subclass.
    """

    # Canvas background per app theme, so the graph doesn't stay a
    # hardcoded dark rectangle inside a light/colorful/accessibility theme.
    _THEME_BACKGROUND = {
        "dark_mode": "#0b0c10",
        "light_mode": "#f5f6fa",
        "colorful_mode": "#ffffff",
        "accessibility_mode": "#ffffff",
    }

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
    # Incremental live-graph updates
    # -----------------------
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
