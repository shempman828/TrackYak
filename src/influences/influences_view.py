from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.asset_paths import icon
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.influences.influence_graph import InfluenceGraphView
from src.influences.influences_dialog import RemoveInfluenceDialog


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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addWidget(self._build_toolbar())

        # Graph view
        self.graph_view = InfluenceGraphView(self.controller)
        self.graph_view.graph_updated.connect(self._refresh_find_index)
        layout.addWidget(self.graph_view)

        self.setLayout(layout)

    def _build_toolbar(self):
        """Build the control strip above the graph: a "Find artist" field on
        the left, view controls (Fit/Legend) next to it, and the rarer edit
        actions (Add/Remove Influence) grouped on the right."""
        toolbar = QFrame()
        toolbar.setObjectName("InfluencesToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(8)

        self.find_field = EntityCompleterEdit("Find artist…", toolbar)
        self.find_field.setObjectName("InfluenceFindField")
        self.find_field.setClearButtonEnabled(True)
        self.find_field.setToolTip("Find an artist and center the graph on them")
        self.find_field.picked.connect(self._focus_typed_artist)
        self.find_field.returnPressed.connect(self._focus_typed_artist)
        toolbar_layout.addWidget(self.find_field, 1)

        toolbar_layout.addWidget(self._vertical_divider())

        self.fit_view_button = QToolButton()
        self.fit_view_button.setIcon(icon("fullscreen.svg"))
        self.fit_view_button.setIconSize(QSize(16, 16))
        self.fit_view_button.setToolTip("Zoom to fit the whole graph on screen")
        self.fit_view_button.clicked.connect(lambda: self.graph_view.fit_to_view())
        toolbar_layout.addWidget(self.fit_view_button)

        self.legend_button = QPushButton("Legend")
        self.legend_button.setProperty("class", "filterChip")
        self.legend_button.setCheckable(True)
        self.legend_button.setCursor(Qt.PointingHandCursor)
        self.legend_button.setChecked(app_config.get_influence_legend_visible())
        self.legend_button.setToolTip("Show or hide the cluster legend overlay. Double-click a legend entry to rename that cluster.")
        self.legend_button.toggled.connect(self.toggle_legend_visible)
        toolbar_layout.addWidget(self.legend_button)

        toolbar_layout.addStretch()

        self.add_influence_button = QPushButton(icon("plus.svg"), "Add Influence")
        self.add_influence_button.setObjectName("PrimaryButton")
        self.add_influence_button.clicked.connect(self.show_add_influence_dialog)
        self.add_influence_button.setToolTip("Add a new influence relationship")
        toolbar_layout.addWidget(self.add_influence_button)

        self.remove_influence_button = QPushButton(icon("minus.svg"), "Remove Influence")
        self.remove_influence_button.setProperty("danger", "true")
        self.remove_influence_button.clicked.connect(self.show_remove_influence_dialog)
        self.remove_influence_button.setToolTip("Remove an influence relationship")
        toolbar_layout.addWidget(self.remove_influence_button)

        toolbar_layout.addWidget(self._vertical_divider())

        self.refresh_button = QToolButton()
        self.refresh_button.setText("↻")
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.refresh_button.setToolTip("Refresh Graph")
        self.refresh_button.clicked.connect(self.refresh_graph)
        toolbar_layout.addWidget(self.refresh_button)

        return toolbar

    @staticmethod
    def _vertical_divider():
        divider = QFrame()
        divider.setObjectName("InfluencesToolbarDivider")
        divider.setFrameShape(QFrame.VLine)
        return divider

    def _refresh_find_index(self):
        """Keep the "Find artist" completer in sync with the graph's
        current node set, whenever InfluenceGraphView reports a change."""
        index = {name: node_id for node_id, name in self.graph_view.node_names.items()}
        self.find_field.set_index(index)

    def _focus_typed_artist(self):
        name = self.find_field.text().strip()
        if not name:
            return
        if not self.graph_view.focus_artist_by_name(name):
            show_status_message(self, f'No artist named "{name}" in the graph.')

    def toggle_legend_visible(self, visible):
        """Show or hide the cluster legend overlay."""
        self.graph_view.set_legend_visible(visible)

    def show_global_view(self):
        """Display entire influence graph"""
        try:
            self.current_mode = "global"
            # No max_nodes parameter anymore
            self.graph_view.display_global_network()

        except RuntimeError as e:
            logger.error(f"Error displaying global view: {e}")
            QMessageBox.critical(self, "Error", f"Failed to display global graph: {e!s}")

    def show_add_influence_dialog(self):
        """Show dialog to add new influence relationship"""
        try:
            from src.influences.influences_dialog import AddInfluenceDialog

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

        except (ImportError, SQLAlchemyError) as e:
            logger.error(f"Error showing add influence dialog: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open influence dialog: {e!s}")

    def add_new_influence_edges(self):
        """Add the most recent influence edges to the existing graph"""
        try:
            # Get the most recent influence relationships (last few)
            influences = self.controller.get.get_all_entities("ArtistInfluence")

            # Take only the last few relationships to avoid adding duplicates
            recent_influences = influences[-5:]  # Get last 5 to be safe

            for influence in recent_influences:
                self.graph_view.add_edge(influence.influencer_id, influence.influenced_id)
                logger.info(f"Added new edge: {influence.influencer_id} -> {influence.influenced_id}")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error adding new influence edges: {e}")
            # If incremental addition fails, fall back to refresh
            QMessageBox.warning(self, "Partial Error", f"Added influence but couldn't update display properly: {e!s}")

    def on_influence_modified(self):
        """Handle complex influence modifications that require full refresh"""
        try:
            # Refresh the entire graph (only for complex changes)
            self.refresh_graph()

            logger.info("Graph fully refreshed after complex modification")

        except RuntimeError as e:
            logger.error(f"Error updating graph after complex modification: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update graph: {e!s}")

    def closeEvent(self, event):
        """Clean up when closing"""
        super().closeEvent(event)

    def refresh_graph(self):
        """Refresh the current graph view"""
        try:
            # Simply refresh the graph without any node limit
            self.graph_view.display_global_network()
            logger.info("Graph refreshed manually")

        except RuntimeError as e:
            logger.error(f"Error refreshing graph: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh graph: {e!s}")

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

                all_influences.append({"influencer_id": inf.influencer_id, "influenced_id": inf.influenced_id, "influencer_name": influencer_name, "influenced_name": influenced_name})
            dialog = RemoveInfluenceDialog(self.controller, all_influences, self)
            if dialog.exec() == QDialog.Accepted:
                # Refresh the graph with the influence removed
                self.on_influence_modified()

        except (SQLAlchemyError, AttributeError) as e:
            logger.error(f"Error showing remove influence dialog: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open remove influence dialog: {e!s}")
