"""Places page: Map and List tabs under one header."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.segmented_control import SegmentedControl
from src.foundation.logger_config import logger
from src.place.map.place_map import MapView
from src.place.place_hierarchy import PLACE_LOAD_OPTIONS
from src.place.place_list import ListView

# QSettings key for the tab (Map/List) the view last showed
_SETTINGS_LAST_TAB = "places/last_tab"

_TAB_MAP, _TAB_LIST = 0, 1


class PlaceView(QWidget):
    """Places page: a Map tab and a List tab under one header, each with its own filters."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_places = []
        self._settings = QSettings()
        # The map is only rebuilt while visible; edits made on the List tab
        # mark it dirty and it catches up the next time it is opened.
        self._map_dirty = True

        self.init_ui()
        self.load_places()

    def init_ui(self):
        """Initialize main UI components"""
        self.setWindowTitle("Place Manager")
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        header = QHBoxLayout()
        self.tab_control = SegmentedControl(["Map", "List"])
        self.tab_control.setItemToolTip(_TAB_MAP, "See places on a world map")
        self.tab_control.setItemToolTip(_TAB_LIST, "Browse, repair, and organize places")
        header.addWidget(self.tab_control)
        header.addStretch()
        self.add_button = QPushButton("+ Add Place")
        self.add_button.setObjectName("PrimaryButton")
        header.addWidget(self.add_button)
        main_layout.addLayout(header)

        self.stacked_widget = QStackedWidget()
        self.map_view = MapView(self.controller, autoload=False)
        self.list_view = ListView(self.controller)
        self.list_view.set_parent_view(self)
        self.stacked_widget.addWidget(self.map_view)
        self.stacked_widget.addWidget(self.list_view)
        main_layout.addWidget(self.stacked_widget, 1)

        self.add_button.clicked.connect(self.list_view.add_place)
        self.map_view.show_in_list_requested.connect(self.show_place_in_list)
        self.map_view.show_unmapped_requested.connect(self._show_unmapped_in_list)

        last_tab = self._settings.value(_SETTINGS_LAST_TAB, _TAB_MAP, type=int)
        if last_tab not in (_TAB_MAP, _TAB_LIST):
            last_tab = _TAB_MAP
        self.tab_control.setCurrentIndex(last_tab)
        self.stacked_widget.setCurrentIndex(last_tab)
        self.tab_control.currentIndexChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        """Remember the tab and show it."""
        self._settings.setValue(_SETTINGS_LAST_TAB, index)
        if index == _TAB_MAP:
            self.show_map_view()
        else:
            self.show_list_view()

    def show_map_view(self):
        """Show the Map tab, redrawing it when list edits made it out of date."""
        if self.tab_control.currentIndex() != _TAB_MAP:
            self.tab_control.setCurrentIndex(_TAB_MAP)  # re-enters via _on_tab_changed
            return
        self.stacked_widget.setCurrentIndex(_TAB_MAP)
        if self._map_dirty:
            self.map_view.refresh_place_types(self.current_places)
            self._map_dirty = False

    def show_list_view(self):
        """Show the List tab."""
        if self.tab_control.currentIndex() != _TAB_LIST:
            self.tab_control.setCurrentIndex(_TAB_LIST)
            return
        self.stacked_widget.setCurrentIndex(_TAB_LIST)

    def show_place_on_map(self, place_id):
        """Switch to the Map tab and open the given place's marker."""
        self.show_map_view()
        self.map_view.focus_place(place_id)

    def show_place_in_list(self, place_id):
        """Switch to the List tab and select the given place."""
        self.show_list_view()
        self.list_view.select_place(place_id)

    def _show_unmapped_in_list(self):
        """Show the List tab filtered to places without coordinates."""
        self.show_list_view()
        self.list_view.show_missing_coordinates()

    def load_places(self):
        """Reload places: redraw the list now, and the map now only when it is visible."""
        try:
            self.current_places = self.controller.get.get_all_entities("Place", load_options=PLACE_LOAD_OPTIONS)
        except SQLAlchemyError:
            logger.exception("Could not load places")
            self.current_places = []
        self.list_view.load_places(self.current_places)
        if self.stacked_widget.currentIndex() == _TAB_MAP:
            self.map_view.refresh_place_types(self.current_places)
            self._map_dirty = False
        else:
            self._map_dirty = True

    def refresh_views(self):
        """Same as load_places."""
        self.load_places()
