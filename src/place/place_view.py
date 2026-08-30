"""View to see places linked to music library"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.place.place_list import ListView
from src.place.place_map import MapView


class PlaceView(QWidget):
    """Main container for place management with toggleable views"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_places = []
        # MapView already loads itself once during construction (see init_ui),
        # so nothing is dirty yet — avoids a redundant redraw on startup.
        self._map_dirty = False

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
        if self._map_dirty:
            # refresh_place_types() already calls load_places() internally,
            # so calling both here would rebuild the map twice.
            self.map_view.refresh_place_types()
            self._map_dirty = False

    def show_list_view(self):
        self.stacked_widget.setCurrentIndex(1)
        self.list_button.setEnabled(False)
        self.map_button.setEnabled(True)

    def load_places(self):
        """Refresh data. The list is always redrawn; the map is only
        redrawn immediately if it's the visible view, otherwise it's
        marked dirty and rebuilt the next time it's opened."""
        self.current_places = self.controller.get.get_all_entities("Place")
        self.list_view.load_places()
        if self.stacked_widget.currentIndex() == 0:
            # refresh_place_types() already calls load_places() internally,
            # so calling both here would rebuild the map twice.
            self.map_view.refresh_place_types()
            self._map_dirty = False
        else:
            self._map_dirty = True

    def refresh_views(self):
        """Alias for load_places — refreshes list data; map lazily on next open."""
        self.load_places()
