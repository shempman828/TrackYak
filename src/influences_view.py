from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsLineItem,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from influence_graph import InfluenceGraphView

from influences_dialog import RemoveInfluenceDialog
from logger_config import logger


class InfluencesView(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_mode = "global"

        self.init_ui()
        self.show_global_view()

    def init_ui(self):
        """Initialize the user interface"""
        layout = QVBoxLayout()

        # Controls section
        control_layout = QHBoxLayout()

        # Configuration components
        config_layout = QVBoxLayout()

        # Action buttons
        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Graph")
        self.refresh_button.clicked.connect(self.refresh_graph)
        self.refresh_button.setToolTip("Refresh the current graph view")

        self.add_influence_button = QPushButton("Add Influence")
        self.add_influence_button.clicked.connect(self.show_add_influence_dialog)
        self.add_influence_button.setToolTip("Add a new influence relationship")

        self.remove_influence_button = QPushButton("Remove Influence")
        self.remove_influence_button.clicked.connect(self.show_remove_influence_dialog)
        self.remove_influence_button.setToolTip("Remove an influence relationship")

        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.add_influence_button)
        button_layout.addWidget(self.remove_influence_button)

        button_layout.addStretch()

        # Assemble control layout
        control_layout.addLayout(config_layout)
        control_layout.addSpacing(20)
        control_layout.addLayout(button_layout)
        control_layout.addStretch()

        # Graph view
        self.graph_view = InfluenceGraphView(self.controller)

        # Add all to main layout
        layout.addLayout(control_layout)
        layout.addWidget(self.graph_view)

        self.setLayout(layout)

    def show_global_view(self):
        """Display entire influence graph"""
        try:
            self.current_mode = "global"
            # No max_nodes parameter anymore
            self.graph_view.display_global_network()

        except Exception as e:
            logger.error(f"Error displaying global view: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to display global graph: {str(e)}"
            )

    def show_add_influence_dialog(self):
        """Show dialog to add new influence relationship"""
        try:
            from influences_dialog import AddInfluenceDialog

            # Get current artists for the dialog
            artists = self.controller.get.get_all_entities("Artist")
            all_artists = [(artist.artist_id, artist.artist_name) for artist in artists]

            dialog = AddInfluenceDialog(self.controller, all_artists, self)
            if dialog.exec() == QDialog.Accepted:
                # Get any newly created artists
                created_artists = dialog.get_created_artists()

                # Add new artists to the existing graph without refreshing
                for artist_id, artist_name in created_artists:
                    self.graph_view.add_single_artist(artist_id, artist_name)

                # Always add the new influence relationship to the graph
                self.add_new_influence_edges()

                logger.info("Added influence relationship incrementally")

        except Exception as e:
            logger.error(f"Error showing add influence dialog: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to open influence dialog: {str(e)}"
            )

    def add_new_influence_edges(self):
        """Add the most recent influence edges to the existing graph"""
        try:
            # Get the most recent influence relationships (last few)
            influences = self.controller.get.get_all_entities("ArtistInfluence")

            # Take only the last few relationships to avoid adding duplicates
            recent_influences = influences[-5:]  # Get last 5 to be safe

            for influence in recent_influences:
                edge_key = (influence.influencer_id, influence.influenced_id)

                # Only add if this edge doesn't already exist in our graph
                if edge_key not in self.graph_view.edges:
                    self.graph_view.edges.append(edge_key)

                    # Create the visual edge if both nodes exist
                    if (
                        influence.influencer_id in self.graph_view.positions
                        and influence.influenced_id in self.graph_view.positions
                    ):
                        source_pos = self.graph_view.positions[influence.influencer_id]
                        target_pos = self.graph_view.positions[influence.influenced_id]

                        line = QGraphicsLineItem(
                            source_pos.x(),
                            source_pos.y(),
                            target_pos.x(),
                            target_pos.y(),
                        )
                        line.setPen(QPen(QColor(100, 100, 100, 150), 1))
                        line.setZValue(-1)
                        self.graph_view.scene.addItem(line)
                        self.graph_view.edge_lines[edge_key] = line

                        logger.info(
                            f"Added new edge: {influence.influencer_id} -> {influence.influenced_id}"
                        )

            # Update node masses for the force layout (since connections changed)
            for node_id in self.graph_view.positions:
                self.graph_view.node_mass[node_id] = 1 + sum(
                    1 for a, b in self.graph_view.edges if a == node_id or b == node_id
                )

            # Restart force layout to incorporate new edges
            self.graph_view.start_force_layout()

        except Exception as e:
            logger.error(f"Error adding new influence edges: {e}")
            # If incremental addition fails, fall back to refresh
            QMessageBox.warning(
                self,
                "Partial Error",
                f"Added influence but couldn't update display properly: {str(e)}",
            )

    def on_influence_modified(self):
        """Handle complex influence modifications that require full refresh"""
        try:
            # Clear cache since relationships changed significantly
            self.cache.clear_all()

            # Refresh the entire graph (only for complex changes)
            self.refresh_graph()

            logger.info("Graph fully refreshed after complex modification")

        except Exception as e:
            logger.error(f"Error updating graph after complex modification: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update graph: {str(e)}")

    def closeEvent(self, event):
        """Clean up when closing"""
        self.graph_view.stop_force_layout()
        super().closeEvent(event)

    def refresh_graph(self):
        """Refresh the current graph view"""
        try:
            # Simply refresh the graph without any node limit
            self.graph_view.display_global_network()
            logger.info("Graph refreshed manually")

        except Exception as e:
            logger.error(f"Error refreshing graph: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh graph: {str(e)}")

    def show_remove_influence_dialog(self):
        """Show dialog to remove influence relationship"""
        try:
            # Get current influence relationships for the dialog
            influences = self.controller.get.get_all_entities("ArtistInfluence")
            all_influences = []
            for inf in influences:
                # Direct access to the related artist objects
                influencer_name = inf.influencer.artist_name
                influenced_name = inf.influenced.artist_name

                all_influences.append(
                    {
                        "influencer_id": inf.influencer_id,
                        "influenced_id": inf.influenced_id,
                        "influencer_name": influencer_name,
                        "influenced_name": influenced_name,
                    }
                )
            dialog = RemoveInfluenceDialog(self.controller, all_influences, self)
            if dialog.exec() == QDialog.Accepted:
                # Refresh the graph with the influence removed
                self.on_influence_modified()

        except Exception as e:
            logger.error(f"Error showing remove influence dialog: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to open remove influence dialog: {str(e)}"
            )
