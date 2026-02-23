"""View to see places linked to music library"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from place_list import ListView
from place_map import MapView


class PlaceView(QWidget):
    """Main container for place management with toggleable views"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_places = []

        self.init_ui()
        self.load_places()

    def init_ui(self):
        """Initialize main UI components"""
        self.setWindowTitle("Place Manager")
        main_layout = QVBoxLayout(self)

        # View toggle controls
        toggle_layout = QHBoxLayout()
        self.map_button = QPushButton("Map View")
        self.list_button = QPushButton("List View")
        self.map_button.clicked.connect(self.show_map_view)
        self.list_button.clicked.connect(self.show_list_view)
        toggle_layout.addWidget(self.map_button)
        toggle_layout.addWidget(self.list_button)

        # Stacked widget for views
        self.stacked_widget = QStackedWidget()
        self.map_view = MapView(self.controller)
        self.list_view = ListView(self.controller)
        self.list_view.set_parent_view(self)

        self.stacked_widget.addWidget(self.map_view)
        self.stacked_widget.addWidget(self.list_view)

        main_layout.addLayout(toggle_layout)
        main_layout.addWidget(self.stacked_widget)

        self.show_map_view()

    def show_map_view(self):
        self.stacked_widget.setCurrentIndex(0)
        self.map_button.setEnabled(False)
        self.list_button.setEnabled(True)

    def show_list_view(self):
        self.stacked_widget.setCurrentIndex(1)
        self.list_button.setEnabled(False)
        self.map_button.setEnabled(True)

    def load_places(self):
        """Refresh data for both views"""
        self.current_places = self.controller.get.get_all_entities("Place")
        self.map_view.load_places()
        self.list_view.load_places()

    def refresh_views(self):
        """Refresh both map and list views and update type filter."""
        self.current_places = self.controller.get.get_all_entities("Place")
        self.map_view.load_places()
        self.list_view.load_places()
        self.map_view.refresh_place_types()
