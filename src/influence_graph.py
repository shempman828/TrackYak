import math
import random
from collections import deque

import networkx as nx
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QMessageBox,
)

from src.influence_artist_node import ArtistNode
from src.logger_config import logger


class InfluenceGraphView(QGraphicsView):
    """
    ForceAtlas2-style layout with Louvain modularity-based communities.
    Retains directional arrows visually but uses undirected physics forces.
    """

    ARROW_ZOOM_THRESHOLD = 0.35

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.scene = QGraphicsScene()
        self.setScene(self.scene)

        # Force-directed layout timer
        self.force_layout_timer = QTimer()
        self.force_layout_timer.timeout.connect(self.update_force_layout)
        self.layout_running = False

        # Graph model
        self.nodes = {}  # node_id -> ArtistNode
        self.node_names = {}  # node_id -> name
        self.edges = []  # list of (source_id, target_id) tuples, directed
        self.positions = {}  # node_id -> QPointF
        self.velocities = {}  # node_id -> QPointF
        self.node_mass = {}  # node_id -> mass for FA2 repulsion
        self.community_id = {}  # node_id -> Louvain community
        self._node_radii = {}  # node_id -> (half_w, half_h) cached each render tick

        # Rendering items
        self.edge_lines = {}  # (source_id, target_id) -> QGraphicsLineItem

        # Layout tuning parameters
        self.attraction_force = 0.1  # FA2 edge attraction
        self.repulsion_force = 1500.0  # FA2 repulsion
        self.damping = 0.85
        self.max_velocity = 5.0
        self.gravity = 0.01  # global gravity
        self.community_force = 0.05  # intra-community cohesion

        # View setup for infinite plane
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        # Remove scrollbars for infinite plane feel
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Infinite scene rect
        self.scene.setSceneRect(-10000, -10000, 20000, 20000)

        self.zoom_factor = 1.15
        self.scale(1.0, 1.0)

        self.influence_scores = {}  # node_id -> influence_score

        # Panning state
        self._panning = False
        self._pan_start_pos = QPointF()

    # -----------------------
    # Interaction / zoom
    # -----------------------
    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self._sync_arrow_visibility()

    def zoom_in(self):
        self.scale(self.zoom_factor, self.zoom_factor)

    def zoom_out(self):
        self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._pan_start_pos = event.position()
            self._last_scene_pos = self.mapToScene(event.position().toPoint())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            current_pos = event.position()
            current_scene_pos = self.mapToScene(current_pos.toPoint())

            # Calculate the movement in scene coordinates
            delta = current_scene_pos - self._last_scene_pos
            self._last_scene_pos = current_scene_pos

            # Move all node positions in the SAME direction as the mouse drag
            # This creates the intuitive "grab and pull" feeling
            for node_id in self.positions:
                self.positions[node_id] += delta  # Changed from -= to +

            # Update the visualization
            self.update_node_positions()
            self.update_edge_positions()

            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # -----------------------
    # Entry point
    # -----------------------
    def display_global_network(self):
        self.stop_force_layout()
        self.clear_scene()
        nodes, edges = self.extract_global_graph()

        # Add this check
        if not nodes:
            QMessageBox.warning(
                self,
                "No Data",
                "No artists with influence relationships found. Add some influence relationships first.",
            )
            return

        self.influence_scores.clear()
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

        self.initialize_random_layout(node_ids)
        self.assign_louvain_communities(node_ids, self.edges)
        self.render_graph()
        self.debug_size_distribution()
        self.start_force_layout()

    # -----------------------
    # Graph extraction
    # -----------------------
    def extract_subgraph(self, center_artist_id, degrees):
        """Extract artists and relationships within n degrees of center artist"""
        try:
            visited = set()
            nodes = []  # (artist_id, artist_name)
            edges = []  # (influencer_id, influenced_id)

            queue = deque([(center_artist_id, 0)])
            visited.add(center_artist_id)

            while queue:
                current_id, current_degree = queue.popleft()

                # Get artist info only if needed for nodes
                # We'll collect all at the end to avoid duplicate lookups

                if current_degree < degrees:
                    # Get influencers (artists who influenced this one)
                    influences = self.controller.get.get_all_entities(
                        "ArtistInfluence", influenced_id=current_id
                    )
                    logger.debug(
                        f"Found {len(influences)} influencers for artist {current_id}"
                    )

                    for influence in influences:
                        edges.append((influence.influencer_id, current_id))
                        if influence.influencer_id not in visited:
                            visited.add(influence.influencer_id)
                            queue.append((influence.influencer_id, current_degree + 1))

                    # Get influenced (artists influenced by this one)
                    influenced = self.controller.get.get_all_entities(
                        "ArtistInfluence", influencer_id=current_id
                    )
                    logger.debug(
                        f"Found {len(influenced)} influenced artists for artist {current_id}"
                    )

                    for influence in influenced:
                        edges.append((current_id, influence.influenced_id))
                        if influence.influenced_id not in visited:
                            visited.add(influence.influenced_id)
                            queue.append((influence.influenced_id, current_degree + 1))

            # Now get artist names for all visited nodes
            for artist_id in visited:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=artist_id
                )
                if artist:
                    nodes.append((artist_id, artist.artist_name))
                else:
                    logger.warning(f"Artist {artist_id} not found in database")

            logger.info(f"Extracted subgraph: {len(nodes)} nodes, {len(edges)} edges")
            return nodes, edges

        except Exception as e:
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

            # Get only the involved artists from the database
            nodes = []
            for artist_id in involved_artist_ids:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=artist_id
                )
                if artist:
                    nodes.append((artist_id, artist.artist_name))
                else:
                    logger.warning(
                        f"Artist {artist_id} not found in database but has influence relationships"
                    )

            logger.info(f"Extracted {len(nodes)} nodes and {len(edges)} edges")
            return nodes, edges

        except Exception as e:
            logger.error(f"Error extracting global graph: {e}")
            return [], []

    # -----------------------
    # Layout initialization & caching
    # -----------------------
    def initialize_random_layout(self, node_ids):
        width, height = 800, 600
        for node_id in node_ids:
            if node_id not in self.positions:
                self.positions[node_id] = QPointF(
                    random.uniform(-width / 2, width / 2),
                    random.uniform(-height / 2, height / 2),
                )
            self.velocities.setdefault(node_id, QPointF(0, 0))
            self.node_mass[node_id] = 1 + sum(
                1 for a, b in self.edges if a == node_id or b == node_id
            )
        current_set = set(node_ids)
        for nid in list(self.positions.keys()):
            if nid not in current_set:
                self.positions.pop(nid, None)
                self.velocities.pop(nid, None)
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
        except Exception as e:
            logger.error(f"Error computing Louvain communities: {e}")
            self.community_id = {nid: 0 for nid in node_ids}

    # -----------------------
    # Force layout
    # -----------------------
    def start_force_layout(self):
        if not self.layout_running:
            self.layout_running = True
            self.force_layout_timer.start(16)

    def stop_force_layout(self):
        if self.layout_running:
            self.layout_running = False
            self.force_layout_timer.stop()

    def update_force_layout(self):
        if not self.layout_running or not self.positions:
            return

        # ── Tuning ───────────────────────────────────────────────────────────
        # Repulsion is now purely for macro-scale cluster separation.
        # Micro-level overlap is handled by _resolve_collisions instead.
        REPULSION_CONST = 80000.0  # stronger global spread
        DAMPING = 0.55
        MAX_SPEED = 50.0  # raised — collisions handle the hard stops
        COMMUNITY_REPULSION_FACTOR = 4.0
        SPRING_REST = 60.0  # slightly longer natural edge length
        # ─────────────────────────────────────────────────────────────────────

        forces = {nid: QPointF(0, 0) for nid in self.positions}
        node_ids = list(self.positions.keys())

        # ── 1. Repulsion (macro-scale, size-aware cutoff) ────────────────────
        for i in range(len(node_ids)):
            id1 = node_ids[i]
            p1 = self.positions[id1]
            c1 = self.community_id.get(id1, 0)
            hw1, hh1 = self._node_radii.get(id1, (30.0, 15.0))

            for j in range(i + 1, len(node_ids)):
                id2 = node_ids[j]
                p2 = self.positions[id2]
                c2 = self.community_id.get(id2, 0)
                hw2, hh2 = self._node_radii.get(id2, (30.0, 15.0))

                dx = p1.x() - p2.x()
                dy = p1.y() - p2.y()

                dist_sq = dx * dx + dy * dy
                if dist_sq < 1.0:
                    dist_sq = 1.0
                    dx, dy = float(i - j), 1.0  # reproducible nudge direction

                dist = math.sqrt(dist_sq)

                force_mag = REPULSION_CONST / dist_sq

                if c1 != c2:
                    force_mag *= COMMUNITY_REPULSION_FACTOR

                fx = force_mag * (dx / dist)
                fy = force_mag * (dy / dist)

                forces[id1] += QPointF(fx, fy)
                forces[id2] -= QPointF(fx, fy)

        # ── 2. Attraction (springs along edges) ──────────────────────────────
        for source_id, target_id in self.edges:
            if source_id in self.positions and target_id in self.positions:
                p1 = self.positions[source_id]
                p2 = self.positions[target_id]

                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                dist = math.sqrt(dx * dx + dy * dy)

                if dist > SPRING_REST:
                    force = self.attraction_force * (dist - SPRING_REST)
                    fx = force * (dx / dist)
                    fy = force * (dy / dist)
                    forces[source_id] += QPointF(fx, fy)
                    forces[target_id] -= QPointF(fx, fy)

        # ── 3. Integration ────────────────────────────────────────────────────
        total_movement = 0
        for nid, force in forces.items():
            v = self.velocities.get(nid, QPointF(0, 0))
            v = (v + force) * DAMPING

            speed = math.hypot(v.x(), v.y())
            if speed > MAX_SPEED:
                v *= MAX_SPEED / speed

            if speed < 0.1:
                v = QPointF(0, 0)

            self.velocities[nid] = v
            self.positions[nid] += v
            total_movement += speed

        # ── 4. Hard collision resolution (bypasses velocity system) ──────────
        self._resolve_collisions()

        self.update_node_positions()
        self.update_edge_positions()

        if total_movement < 0.5:
            self.stop_force_layout()
            logger.info("Graph settled.")

    # -----------------------
    # Scene & rendering
    # -----------------------
    def render_graph(self):
        """Render nodes and edges with arrow indicators for direction."""
        try:
            # Calculate influence scores if not already done
            if not self.influence_scores and self.positions and self.edges:
                self.calculate_influence_scores(list(self.positions.keys()), self.edges)

            # Make sure every node in positions has an ArtistNode in scene
            for node_id, pos in self.positions.items():
                name = self.node_names.get(node_id, f"Artist {node_id}")

                # Calculate node size based on influence
                node_size = self.get_node_size(node_id)
                width = node_size
                height = node_size * 0.5  # Maintain aspect ratio

                if node_id in self.nodes:
                    # Update existing node size
                    node_item = self.nodes[node_id]
                    node_item.setRect(-width / 2, -height / 2, width, height)

                    # Update text if changed
                    if getattr(node_item, "artist_name", None) != name:
                        try:
                            node_item.update_text(name)
                        except Exception:
                            pass

                    node_item.setPos(pos)
                    if node_item.scene() is None:
                        self.scene.addItem(node_item)
                else:
                    # Create new node with calculated size
                    node_item = ArtistNode(
                        node_id, name, pos.x(), pos.y(), width, height
                    )
                    self.nodes[node_id] = node_item
                    node_item.setZValue(1)  # Nodes above edges
                    self.scene.addItem(node_item)

            # Remove any node items that are no longer present
            current_nodes = set(self.positions.keys())
            orphan_ids = [nid for nid in self.nodes if nid not in current_nodes]
            for nid in orphan_ids:
                try:
                    self.scene.removeItem(self.nodes[nid])
                except Exception:
                    pass
                self.nodes.pop(nid, None)
                self.velocities.pop(nid, None)
                self.positions.pop(nid, None)
            self._rebuild_node_radii()
            # Update/create edge lines with arrows and remove obsolete ones
            existing_edge_keys = set(self.edge_lines.keys())
            desired_edge_keys = set(self.edges)

            # Remove edges not desired
            for key in existing_edge_keys - desired_edge_keys:
                try:
                    # Remove both line and arrow if they exist
                    if key in self.edge_lines:
                        line_item, arrow_item = self.edge_lines[key]
                        self.scene.removeItem(line_item)
                        if arrow_item:
                            self.scene.removeItem(arrow_item)
                except Exception:
                    pass
                self.edge_lines.pop(key, None)

            # Create new edges if missing, and update existing ones
            for source_id, target_id in self.edges:
                if source_id in self.positions and target_id in self.positions:
                    source_pos = self.positions[source_id]
                    target_pos = self.positions[target_id]
                    key = (source_id, target_id)

                    if key in self.edge_lines:
                        # Update existing edge
                        line_item, arrow_item = self.edge_lines[key]
                        line_item.setLine(
                            source_pos.x(),
                            source_pos.y(),
                            target_pos.x(),
                            target_pos.y(),
                        )

                        # Update arrow position
                        if arrow_item:
                            self.update_arrow_position(
                                arrow_item, source_pos, target_pos
                            )
                    else:
                        # Create new edge with arrow
                        line_item, arrow_item = self.create_arrow_line(
                            source_pos, target_pos, source_id, target_id
                        )
                        if line_item:
                            line_item.setZValue(0)  # Edges below nodes
                            self.scene.addItem(line_item)
                        if arrow_item:
                            arrow_item.setZValue(0)  # Arrows same level as edges
                            self.scene.addItem(arrow_item)

                        self.edge_lines[key] = (line_item, arrow_item)
                else:
                    logger.debug(f"Skipping edge {source_id}->{target_id}: missing pos")

            self.debug_graph_structure()

        except Exception as e:
            logger.error(f"Error rendering graph: {e}")

    def _rebuild_node_radii(self):
        """Cache each node's half-dimensions for the physics loop."""
        self._node_radii = {}
        for node_id, node_item in self.nodes.items():
            r = node_item.rect()
            self._node_radii[node_id] = (abs(r.width()) / 2.0, abs(r.height()) / 2.0)

    def create_arrow_line(self, start_pos, end_pos, source_id, target_id):
        """
        Create a directed edge from start_pos to end_pos.

        Visual design for large graphs (hundreds-to-thousands of nodes):
        - Arrow placed near the TARGET node boundary, not the midpoint,
        so direction is unambiguous even in dense clusters.
        - Edge opacity is proportional to the source node's influence score
        (high-influence edges are prominent; weak ones fade to 25%).
        - Arrow inherits the same opacity so it never conflicts with the line.
        """
        try:
            dx = end_pos.x() - start_pos.x()
            dy = end_pos.y() - start_pos.y()
            length = math.sqrt(dx * dx + dy * dy)

            if length < 1:
                # Coincident nodes — return invisible placeholder
                line = QGraphicsLineItem(
                    start_pos.x(), start_pos.y(), end_pos.x(), end_pos.y()
                )
                line.setPen(QPen(QColor(100, 100, 100, 0), 0))
                return line, None

            # --- Influence-based opacity ---
            # Source node drives visibility: important influencers get opaque edges.
            src_score = self.influence_scores.get(source_id, 0)
            max_score = (
                max(self.influence_scores.values()) if self.influence_scores else 1
            )
            # Normalise to [0,1], then map to [MIN_ALPHA, MAX_ALPHA]
            MIN_ALPHA, MAX_ALPHA = 45, 210
            t = (src_score / max_score) ** 0.5  # sqrt eases the curve for large ranges
            edge_alpha = int(MIN_ALPHA + t * (MAX_ALPHA - MIN_ALPHA))

            # --- Line ---
            line = QGraphicsLineItem(
                start_pos.x(), start_pos.y(), end_pos.x(), end_pos.y()
            )
            pen = QPen(QColor(160, 160, 175, edge_alpha), 1.2)
            pen.setCapStyle(Qt.RoundCap)
            line.setPen(pen)

            # --- Arrow placement: offset from target boundary ---
            # Estimate how far to pull back along the edge so the tip lands
            # just outside (or at) the target node's rectangular boundary.
            if target_id in self.nodes:
                t_rect = self.nodes[target_id].rect()
                half_w = abs(t_rect.width()) / 2
                half_h = abs(t_rect.height()) / 2
            else:
                half_w = 30
                half_h = 15

            # Distance from node centre to its boundary along this edge direction
            ux, uy = dx / length, dy / length
            if abs(ux) > 1e-6:
                t_w = half_w / abs(ux)
            else:
                t_w = float("inf")
            if abs(uy) > 1e-6:
                t_h = half_h / abs(uy)
            else:
                t_h = float("inf")
            boundary_offset = min(t_w, t_h) + 4  # 4px gap so tip clears the border

            # Clamp so the arrow doesn't jump behind the source node
            boundary_offset = min(boundary_offset, length * 0.45)

            arrow_x = end_pos.x() - ux * boundary_offset
            arrow_y = end_pos.y() - uy * boundary_offset

            # --- Arrowhead ---
            arrow_size = 7
            arrow_polygon = QPolygonF(
                [
                    QPointF(0, 0),
                    QPointF(-arrow_size * 1.6, -arrow_size * 0.6),
                    QPointF(-arrow_size * 1.6, arrow_size * 0.6),
                ]
            )

            arrow = QGraphicsPolygonItem(arrow_polygon)
            arrow_color = QColor(153, 234, 133, edge_alpha)
            arrow.setBrush(QBrush(arrow_color))
            arrow.setPen(QPen(Qt.NoPen))
            arrow.setPos(arrow_x, arrow_y)
            arrow_angle = math.degrees(math.atan2(dy, dx))
            arrow.setRotation(arrow_angle)

            return line, arrow

        except Exception as e:
            logger.error(f"Error creating arrow line: {e}")
            line = QGraphicsLineItem(
                start_pos.x(), start_pos.y(), end_pos.x(), end_pos.y()
            )
            line.setPen(QPen(QColor(100, 100, 100, 150), 1))
            return line, None

    def _resolve_collisions(self):
        """
        Directly separate any overlapping nodes by pushing each half the overlap
        distance apart. This runs AFTER force integration so it cannot be
        throttled by MAX_SPEED or damping.

        Uses axis-aligned bounding box (AABB) overlap — cheap and correct for
        rectangular nodes.  GAP adds breathing room so nodes settle with space
        between them rather than just touching.
        """
        GAP = 18  # minimum clear space between node edges (px)
        ITERATIONS = 3  # multiple passes per tick converges faster for clusters

        node_ids = list(self.positions.keys())

        for _ in range(ITERATIONS):
            for i in range(len(node_ids)):
                id1 = node_ids[i]
                p1 = self.positions[id1]
                hw1, hh1 = self._node_radii.get(id1, (30.0, 15.0))

                for j in range(i + 1, len(node_ids)):
                    id2 = node_ids[j]
                    p2 = self.positions[id2]
                    hw2, hh2 = self._node_radii.get(id2, (30.0, 15.0))

                    # AABB overlap test
                    overlap_x = (hw1 + hw2 + GAP) - abs(p1.x() - p2.x())
                    overlap_y = (hh1 + hh2 + GAP) - abs(p1.y() - p2.y())

                    # Only colliding if BOTH axes overlap
                    if overlap_x <= 0 or overlap_y <= 0:
                        continue

                    # Separate along the axis of least overlap (minimal movement)
                    if overlap_x < overlap_y:
                        # Push apart horizontally
                        push = overlap_x / 2.0
                        if p1.x() >= p2.x():
                            self.positions[id1] = QPointF(p1.x() + push, p1.y())
                            self.positions[id2] = QPointF(p2.x() - push, p2.y())
                        else:
                            self.positions[id1] = QPointF(p1.x() - push, p1.y())
                            self.positions[id2] = QPointF(p2.x() + push, p2.y())
                    else:
                        # Push apart vertically
                        push = overlap_y / 2.0
                        if p1.y() >= p2.y():
                            self.positions[id1] = QPointF(p1.x(), p1.y() + push)
                            self.positions[id2] = QPointF(p2.x(), p2.y() - push)
                        else:
                            self.positions[id1] = QPointF(p1.x(), p1.y() - push)
                            self.positions[id2] = QPointF(p2.x(), p2.y() + push)

                    # Re-read p1 since it may have been updated this iteration
                    p1 = self.positions[id1]

    def update_arrow_position(self, arrow_item, start_pos, end_pos, target_id=None):
        """
        Update arrowhead to stay near the target node boundary.
        Mirrors the placement logic in create_arrow_line.
        """
        try:
            dx = end_pos.x() - start_pos.x()
            dy = end_pos.y() - start_pos.y()
            length = math.sqrt(dx * dx + dy * dy)

            if length < 10:
                arrow_item.setVisible(False)
                return

            ux, uy = dx / length, dy / length

            if target_id and target_id in self.nodes:
                t_rect = self.nodes[target_id].rect()
                half_w = abs(t_rect.width()) / 2
                half_h = abs(t_rect.height()) / 2
            else:
                half_w = 30
                half_h = 15

            if abs(ux) > 1e-6:
                t_w = half_w / abs(ux)
            else:
                t_w = float("inf")
            if abs(uy) > 1e-6:
                t_h = half_h / abs(uy)
            else:
                t_h = float("inf")
            boundary_offset = min(t_w, t_h) + 4
            boundary_offset = min(boundary_offset, length * 0.45)

            arrow_x = end_pos.x() - ux * boundary_offset
            arrow_y = end_pos.y() - uy * boundary_offset

            arrow_item.setPos(arrow_x, arrow_y)
            arrow_angle = math.degrees(math.atan2(dy, dx))
            arrow_item.setRotation(arrow_angle)

            # Respect zoom-based visibility (set by _sync_arrow_visibility)
            # Only make it visible if it was previously hidden due to length,
            # not due to zoom — zoom visibility is managed separately.
            if not arrow_item.isVisible():
                zoom = self.transform().m11()
                arrow_item.setVisible(zoom >= self.ARROW_ZOOM_THRESHOLD)

        except Exception as e:
            logger.error(f"Error updating arrow position: {e}")

    def update_node_positions(self):
        """Update positions of node QGraphicsItems from self.positions"""
        for node_id, pos in self.positions.items():
            if node_id in self.nodes:
                try:
                    self.nodes[node_id].setPos(pos)
                except Exception:
                    logger.debug(f"Failed to setPos for node {node_id}")

    def update_edge_positions(self):
        """Update the positions of existing edge lines and arrows."""
        for (source_id, target_id), (line_item, arrow_item) in self.edge_lines.items():
            if source_id in self.positions and target_id in self.positions:
                sp = self.positions[source_id]
                tp = self.positions[target_id]
                try:
                    line_item.setLine(sp.x(), sp.y(), tp.x(), tp.y())
                    if arrow_item:
                        self.update_arrow_position(arrow_item, sp, tp, target_id)
                except Exception:
                    logger.debug(f"Failed to update edge line {source_id}->{target_id}")

    def _sync_arrow_visibility(self):
        """Show/hide arrowheads based on current zoom level to reduce clutter at scale."""
        zoom = self.transform().m11()
        visible = zoom >= self.ARROW_ZOOM_THRESHOLD
        for _, (_, arrow_item) in self.edge_lines.items():
            if arrow_item:
                arrow_item.setVisible(visible)

    # -----------------------
    # Utilities
    # -----------------------
    def clear_scene(self):
        """Clear the entire scene and reset all graph data"""
        # Stop force layout first
        self.stop_force_layout()

        # Clear all items from scene
        self.scene.clear()

        # Reset all graph data structures
        self.nodes.clear()
        self.edge_lines.clear()
        self.positions.clear()
        self.velocities.clear()
        self.node_mass.clear()
        self.community_id.clear()

    def debug_graph_structure(self):
        """Log summary info about the graph"""
        try:
            logger.info(
                f"Graph has {len(self.nodes)} nodes and {len(self.edges)} edges"
            )

            # Count connections per node
            connection_counts = {nid: 0 for nid in self.nodes.keys()}
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
                node_name = (
                    self.nodes[node_id].artist_name
                    if node_id in self.nodes
                    and hasattr(self.nodes[node_id], "artist_name")
                    else self.node_names.get(node_id, f"Artist {node_id}")
                )
                logger.info(f"  {node_name}: {count} connections")
        except Exception as e:
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

        except Exception as e:
            logger.error(f"Error checking database relationships: {e}")
            return False

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
            self.influence_scores = {}

            for node_id in node_ids:
                if node_id in G:
                    descendants = nx.descendants(G, node_id)
                    self.influence_scores[node_id] = len(descendants)
                else:
                    self.influence_scores[node_id] = 0

            # -----------------------------
            # 2. Decayed PageRank influence weighting
            # -----------------------------
            try:
                # Your previously added function:
                # compute_decayed_pagerank(G, decay_factor=0.85)
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

            except Exception as e:
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

        except Exception as e:
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

            # If this artist is already in the graph, just update the name
            if artist_id in self.positions:
                self.node_names[artist_id] = artist_name
                if artist_id in self.nodes:
                    self.nodes[artist_id].update_text(artist_name)
                return

            # Add new artist to the graph data structures
            self.node_names[artist_id] = artist_name

            # Rest of the method remains the same...
            width, height = 800, 600
            self.positions[artist_id] = QPointF(
                random.uniform(-width / 2, width / 2),
                random.uniform(-height / 2, height / 2),
            )
            self.velocities[artist_id] = QPointF(0, 0)

            # Calculate initial mass (will be updated properly later)
            self.node_mass[artist_id] = 1

            # Create and add the node to the scene
            node_size = self.get_node_size(artist_id)
            width = node_size
            height = node_size * 0.5

            node_item = ArtistNode(
                artist_id,
                artist_name,
                self.positions[artist_id].x(),
                self.positions[artist_id].y(),
                width,
                height,
            )
            self.nodes[artist_id] = node_item
            node_item.setZValue(0)
            self.scene.addItem(node_item)

            # Restart force layout to incorporate the new node
            self.start_force_layout()

        except Exception as e:
            logger.error(f"Error adding single artist {artist_id}: {e}")

    def debug_size_distribution(self):
        """Log the size distribution for analysis"""
        if not self.influence_scores:
            return

        sizes = []
        for node_id in self.positions.keys():
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

        except Exception as e:
            logger.error(f"Error computing PageRank: {e}")
            # Return 0.0 for all nodes on failure
            return {n: 0.0 for n in G.nodes()}
