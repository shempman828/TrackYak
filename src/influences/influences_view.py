import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QMessageBox, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.asset_paths import icon
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.influences.graph.influence_graph import InfluenceGraphView
from src.influences.influences_dialog import AddInfluenceDialog, RemoveInfluenceDialog


class InfluencesView(QWidget):
    """Influences tab: toolbar (find, fit, legend, add/remove, refresh) over the influence graph."""

    _DOUBLE_FOCUS_WINDOW = 0.3  # seconds

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._last_focus = None  # (name, monotonic time) of the last focus

        self.init_ui()
        self.show_global_view()

    def init_ui(self):
        """Build the toolbar and the graph view."""
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addWidget(self._build_toolbar())

        # Stretch 1, or the Preferred toolbar takes half the spare height.
        self.graph_view = InfluenceGraphView(self.controller)
        self.graph_view.graph_updated.connect(self._refresh_find_index)
        self.graph_view.busy_changed.connect(self._on_graph_busy)
        layout.addWidget(self.graph_view, 1)

        self.setLayout(layout)

    def _build_toolbar(self):
        """Build the toolbar: find field, view controls, then edit actions and refresh on the right."""
        toolbar = QFrame()
        toolbar.setObjectName("InfluencesToolbar")
        toolbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
        self.fit_view_button.setToolTip("Fit to View: zoom to fit the whole graph on screen")
        self.fit_view_button.setAccessibleName("Fit to View")
        self.fit_view_button.clicked.connect(lambda: self.graph_view.fit_to_view())
        toolbar_layout.addWidget(self.fit_view_button)

        self.legend_button = QPushButton("Legend")
        self.legend_button.setProperty("class", "filterChip")
        self.legend_button.setCheckable(True)
        self.legend_button.setCursor(Qt.PointingHandCursor)
        self.legend_button.setChecked(app_config.get_influence_legend_visible())
        self.legend_button.setToolTip("Show or hide the cluster legend overlay. Use its Rename… button to name clusters.")
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
        self.refresh_button.setToolTip("Refresh Graph: rebuild the layout and clusters from the database")
        self.refresh_button.setAccessibleName("Refresh Graph")
        self.refresh_button.clicked.connect(self.refresh_graph)
        toolbar_layout.addWidget(self.refresh_button)

        return toolbar

    @staticmethod
    def _vertical_divider():
        divider = QFrame()
        divider.setObjectName("InfluencesToolbarDivider")
        divider.setFrameShape(QFrame.VLine)
        return divider

    def _on_graph_busy(self, busy):
        """Disable Refresh while the graph recomputes."""
        self.refresh_button.setEnabled(not busy)

    def _refresh_find_index(self):
        """Sync the "Find artist" completer with the graph's current nodes."""
        index = {name: node_id for node_id, name in self.graph_view.node_names.items()}
        self.find_field.set_index(index)
        self._last_focus = None

    def _focus_typed_artist(self):
        """Center the graph on the typed artist, or show a status when not found."""
        name = self.find_field.text().strip()
        if not name:
            return
        # A completer pick plus Enter fires both picked and returnPressed for one key press.
        now = time.monotonic()
        last = self._last_focus
        if last and last[0] == name and now - last[1] < self._DOUBLE_FOCUS_WINDOW:
            return
        self._last_focus = (name, now)
        if not self.graph_view.focus_artist_by_name(name):
            show_status_message(self, f'No artist named "{name}" in the graph.')

    def toggle_legend_visible(self, visible):
        """Show or hide the cluster legend overlay."""
        self.graph_view.set_legend_visible(visible)

    def show_global_view(self):
        """Display the entire influence graph."""
        self.refresh_graph()

    def show_add_influence_dialog(self):
        """Open the Add Influence dialog and add the new relationship to the live graph."""
        try:
            artists = self.controller.get.get_all_entities("Artist")
        except SQLAlchemyError as e:
            logger.error(f"Error loading artists for add influence dialog: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open influence dialog: {e!s}")
            return

        all_artists = [(artist.artist_id, artist.artist_name) for artist in artists]
        dialog = AddInfluenceDialog(self.controller, all_artists, self)
        if dialog.exec() != QDialog.Accepted or dialog.added_influence is None:
            return
        influencer, influenced = dialog.added_influence
        self.graph_view.add_influence(influencer, influenced)
        logger.info(f"Added influence {influencer[0]} -> {influenced[0]} incrementally")

    def refresh_graph(self):
        """Rebuild the graph from the database in the background."""
        try:
            self.graph_view.display_global_network()
        except RuntimeError as e:
            logger.error(f"Error refreshing graph: {e}")
            QMessageBox.critical(self, "Error", f"Failed to refresh graph: {e!s}")

    def _load_influence_rows(self):
        """Return one dict per stored influence for the Remove dialog, skipping orphan rows."""
        rows = []
        for inf in self.controller.get.get_all_entities("ArtistInfluence"):
            if inf.influencer is None or inf.influenced is None:
                logger.warning(f"Skipping orphan influence {inf.influencer_id} -> {inf.influenced_id}")
                continue
            rows.append(
                {
                    "influencer_id": inf.influencer_id,
                    "influenced_id": inf.influenced_id,
                    "influencer_name": inf.influencer.artist_name,
                    "influenced_name": inf.influenced.artist_name,
                    "description": inf.description,
                }
            )
        return rows

    def show_remove_influence_dialog(self):
        """Open the Remove Influence dialog and rebuild the graph after a removal."""
        try:
            all_influences = self._load_influence_rows()
        except SQLAlchemyError as e:
            logger.error(f"Error showing remove influence dialog: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open remove influence dialog: {e!s}")
            return

        dialog = RemoveInfluenceDialog(self.controller, all_influences, self)
        if dialog.exec() == QDialog.Accepted and dialog.removed_influence is not None:
            removed = dialog.removed_influence
            show_status_message(self, f"Removed influence: {removed['influencer_name']} → {removed['influenced_name']}")
            # A removal can orphan a node or split a community, so recompute in full.
            self.refresh_graph()
