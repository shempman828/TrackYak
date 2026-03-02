import html as html_escape
import json
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QObject, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger
from src.place_assoc_details import AssociationDetailsDialog
from src.place_map_filter import MultiSelectWidget


class MapView(QWidget):
    """Interactive map display with color-coded markers based on place type."""

    # Define color mapping for place types
    COLOR_MAPPING = {
        "country": "#2ecc71",  # Green
        "state": "#e67e22",  # Orange
        "county": "#f1c40f",  # Yellow/Gold
        "city": "#3498db",  # Blue
        "district": "#9b59b6",  # Purple
        "building": "#7f8c8d",  # Gray/Stone
        "room": "#1abc9c",  # Teal
        "point of interest": "#e74c3c",  # Red
        "default": "#8599ea",  # Your theme accent color
    }

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.selected_types = set()  # Track selected types
        self.all_place_types = set()  # Track all available types
        self.init_ui()
        self.setup_js_communication()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 1. Header with Toggle Button
        header_layout = QHBoxLayout()
        self.toggle_filter_button = QPushButton("Show Filters")
        self.toggle_filter_button.setCheckable(True)
        self.toggle_filter_button.setChecked(False)
        self.toggle_filter_button.clicked.connect(self.toggle_filter_visibility)

        header_layout.addWidget(self.toggle_filter_button)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # 2. Filter Container (The part that hides/shows)
        self.filter_container = QWidget()
        self.filter_container.setVisible(False)  # Hide by default
        filter_main_layout = QVBoxLayout(self.filter_container)
        filter_main_layout.setContentsMargins(0, 0, 0, 5)

        filter_controls = QHBoxLayout()
        filter_controls.addWidget(QLabel("Filter by type:"))

        self.multi_select_widget = MultiSelectWidget()
        self.multi_select_widget.selection_changed.connect(self.apply_filter)
        filter_controls.addWidget(self.multi_select_widget, 1)

        self.refresh_types_button = QPushButton("Refresh Types")
        self.refresh_types_button.clicked.connect(self.refresh_place_types)
        filter_controls.addWidget(self.refresh_types_button)

        filter_main_layout.addLayout(filter_controls)
        layout.addWidget(self.filter_container)

        # 3. Map Widget
        self.map_widget = QWebEngineView()
        layout.addWidget(self.map_widget)

        self.refresh_place_types()

    def _get_color_for_type(self, place_type: str) -> str:
        """Returns a mapped color or generates a stable dynamic color for new types."""
        if not place_type:
            return self.COLOR_MAPPING["default"]

        type_lower = place_type.lower().strip()

        # 1. Check if we have a predefined color
        if type_lower in self.COLOR_MAPPING:
            return self.COLOR_MAPPING[type_lower]

        # 2. Fallback: Generate a stable color based on the text hash
        # This ensures "Kitchen" always gets the same color without hardcoding it.
        import hashlib

        hash_val = int(hashlib.md5(type_lower.encode()).hexdigest(), 16)

        # A palette of vibrant modern colors
        palette = [
            "#ff7675",
            "#6c5ce7",
            "#00b894",
            "#fdcb6e",
            "#e84393",
            "#00cec9",
            "#fab1a0",
            "#a29bfe",
        ]
        return palette[hash_val % len(palette)]

    def toggle_filter_visibility(self):
        """Toggles the filter container visibility and updates button text."""
        is_visible = self.filter_container.isVisible()
        self.filter_container.setVisible(not is_visible)

        if not is_visible:
            self.toggle_filter_button.setText("Hide Filters")
        else:
            self.toggle_filter_button.setText("Show Filters")

    def refresh_place_types(self):
        """Dynamically load place types from the database."""
        try:
            # Get all places and extract unique types
            places = self.controller.get.get_all_entities("Place")
            unique_types = set()

            for place in places:
                if place.place_type and place.place_type.strip():
                    # Clean up the type name
                    type_name = place.place_type.strip().title()
                    unique_types.add(type_name)

            self.all_place_types = unique_types

            # Update the multi-select widget
            self.multi_select_widget.set_items(
                list(unique_types), default_selected=True
            )

            # Store the selected types
            self.selected_types = set(self.multi_select_widget.get_selected_items())

            logger.info(
                f"Refreshed place types: {len(unique_types)} unique types found, "
                f"{len(self.selected_types)} selected"
            )

            # Reload places with current filter
            self.load_places()

        except Exception as e:
            logger.error(f"Error refreshing place types: {str(e)}")
            QMessageBox.warning(self, "Error", "Failed to refresh place types")

    def setup_js_communication(self):
        """Set up JavaScript to Python communication."""

        class Bridge(QObject):
            def __init__(self, map_view):
                super().__init__()
                self.map_view = map_view

            @Slot(str)
            def handle_js_message(self, message):
                try:
                    data = json.loads(message)
                    if data.get("type") == "viewAssociations":
                        place_id = data.get("placeId")
                        self.map_view.show_associations_for_place(place_id)
                except Exception as e:
                    logger.error(f"Error handling JS message: {str(e)}")

        self.bridge = Bridge(self)
        self.channel = QWebChannel()
        self.map_widget.page().setWebChannel(self.channel)
        self.channel.registerObject("pyBridge", self.bridge)

    def apply_filter(self, selected_types):
        """Apply filter to map markers based on selected types."""
        self.selected_types = set(selected_types)
        self.load_places()

    def load_places(self, places: List[Dict] = None):
        try:
            if places is None:
                raw_places = self.controller.get.get_all_entities("Place")
                places = [self._create_place_data(p) for p in raw_places]

            # Ensure we always filter, even if selected_types is empty
            filtered_places = []
            for place in places:
                # MUST use .strip().title() to match refresh_place_types logic
                raw_type = place.get("type") or ""
                place_type = raw_type.strip().title()

                if place_type in self.selected_types:
                    filtered_places.append(place)

            # Always generate map with the filtered list
            self.generate_map(filtered_places)

        except Exception as e:
            logger.error(f"Failed to load places: {str(e)}", exc_info=True)

    def generate_map(self, places: List[Dict]):
        """Create and display Leaflet map with color-coded markers."""
        try:
            valid_places = [
                p for p in places if p["lat"] is not None and p["lon"] is not None
            ]

            if valid_places:
                avg_lat = sum(p["lat"] for p in valid_places) / len(valid_places)
                avg_lon = sum(p["lon"] for p in valid_places) / len(valid_places)
                zoom_level = 4
            else:
                avg_lat, avg_lon, zoom_level = 30, 0, 2

            html_content = self._create_map_html(
                valid_places, avg_lat, avg_lon, zoom_level
            )
            self.map_widget.setHtml(html_content)

            logger.info(
                f"Map generated with {len(valid_places)} valid places "
                f"(filter: {len(self.selected_types)} types selected)"
            )

        except Exception as e:
            logger.error(f"Map generation failed: {str(e)}", exc_info=True)
            self.show_fallback_map()

    def _create_map_html(
        self, places: List[Dict], center_lat: float, center_lon: float, zoom: int
    ) -> str:
        """Create complete HTML content for the map with WebChannel support."""

        # Load HTML template from file
        template_path = (
            Path(__file__).parent.parent / "assets" / "place_map_template.html"
        )
        if template_path.exists():
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template = f.read()
            except Exception as e:
                logger.error(f"Failed to load HTML template: {str(e)}")
                template = self._get_fallback_template()
        else:
            logger.warning("HTML template not found, using fallback")
            template = self._get_fallback_template()

        # Create markers JavaScript code
        markers_js = self._create_markers_js(places)

        # Generate bounds JavaScript
        bounds_js = self._create_bounds_js(places)

        # Use .replace() instead of .format() to avoid CSS brace conflicts
        html = template.replace("{center_lat}", str(center_lat))
        html = html.replace("{center_lon}", str(center_lon))
        html = html.replace("{zoom}", str(zoom))
        html = html.replace("{markers_js}", markers_js)
        html = html.replace("{bounds_js}", bounds_js)

        return html

    def _create_markers_js(self, places: List[Dict]) -> str:
        """Create JavaScript code for map markers with dynamic coloring."""
        markers_js = ""
        for place in places:
            # Use the new dynamic color helper
            marker_color = self._get_color_for_type(place.get("type", ""))

            popup_content = self._create_popup_content(place)

            markers_js += f"""
                L.marker([{place["lat"]}, {place["lon"]}], {{
                    icon: L.divIcon({{
                        className: 'custom-div-icon',
                        html: '<div style="background-color: {marker_color}; width: 18px; height: 18px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div>',
                        iconSize: [18, 18],
                        iconAnchor: [9, 9]
                    }})
                }}).addTo(map)
                .bindPopup(`{popup_content}`)
                .bindTooltip('{place["name"]}');
                """
        return markers_js

    def _create_bounds_js(self, places: List[Dict]) -> str:
        """Create JavaScript code for map bounds."""
        if not places:
            return ""

        bounds_js = "var bounds = L.latLngBounds([\n"
        for place in places:
            bounds_js += f"    [{place['lat']}, {place['lon']}],\n"
        bounds_js += "]);\nmap.fitBounds(bounds, { padding: [20, 20] });"
        return bounds_js

    def _get_fallback_template(self) -> str:
        """Get a fallback HTML template if file is not found."""
        return """<!DOCTYPE html>
<html>
<head>
    <title>Places Map</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin=""/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
            integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
            crossorigin=""></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        body { margin: 0; padding: 0; }
        #map { height: 100vh; width: 100%; }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            window.pyBridge = channel.objects.pyBridge;
        }});
        window.viewAssociations = function(placeId) {{
            if (window.pyBridge) {{
                window.pyBridge.handle_js_message(JSON.stringify({{
                    type: 'viewAssociations',
                    placeId: placeId
                }}));
            }}
        }};
        var map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '© OpenStreetMap, © CARTO',
            maxZoom: 18
        }}).addTo(map);
        {markers_js}
        {bounds_js}
    </script>
</body>
</html>"""

    def _create_popup_content(self, place: Dict) -> str:
        """Generate popup content with view associations button."""
        # Escape HTML in the description to prevent XSS
        description = ""
        if place.get("description"):
            description = html_escape.escape(place["description"])

        content = [
            f"<h3 style='margin: 0 0 8px 0; color: #8599ea;'>{place['name']}</h3>",
            "<div style='border-bottom: 1px solid rgba(133, 153, 234, 0.3); padding-bottom: 8px; margin-bottom: 8px;'>",
            f"<strong>Type:</strong> {place['type']}<br>",
            f"<strong>Coordinates:</strong> {place['lat']:.4f}, {place['lon']:.4f}",
            "</div>",
        ]

        if description:
            content.append(
                f"<div style='margin-top: 8px;'><strong>Description:</strong><br>{description[:200]}{'...' if len(description) > 200 else ''}</div>"
            )

        # Add view associations button
        content.append(
            f"""<div style='margin-top: 12px; text-align: center;'>
                <button onclick='viewAssociations({place["id"]})'
                        style='background-color: #8599ea; color: white; border: none;
                               padding: 6px 12px; border-radius: 4px; cursor: pointer;'>
                    View Associations
                </button>
            </div>"""
        )

        return "".join(content)

    def show_associations_for_place(self, place_id):
        """Show associations for a place from map pin click."""
        try:
            place = self.controller.get.get_entity_object(
                "Place", place_id=int(place_id)
            )
            if place:
                dialog = AssociationDetailsDialog(self.controller, place, self)
                dialog.exec_()
            else:
                logger.error(f"Place with ID {place_id} not found")
                QMessageBox.warning(
                    self, "Not Found", f"Place with ID {place_id} not found"
                )
        except Exception as e:
            logger.error(f"Error showing associations: {str(e)}")
            QMessageBox.critical(
                self, "Error", f"Failed to show associations: {str(e)}"
            )

    def _create_place_data(self, raw_place) -> Dict:
        """Convert raw place data to UI format."""
        lat = raw_place.place_latitude
        lon = raw_place.place_longitude

        # Convert to float if they're strings, or set to None if invalid
        try:
            lat = float(lat) if lat is not None and str(lat).strip() else None
        except (ValueError, TypeError):
            lat = None

        try:
            lon = float(lon) if lon is not None and str(lon).strip() else None
        except (ValueError, TypeError):
            lon = None

        return {
            "id": raw_place.place_id,
            "name": raw_place.place_name,
            "type": raw_place.place_type,
            "lat": lat,
            "lon": lon,
            "description": raw_place.place_description,
        }

    def show_fallback_map(self):
        """Display a simple fallback message."""
        fallback_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .fallback-content {
                    text-align: center;
                    padding: 20px;
                }
            </style>
        </head>
        <body>
            <div class="fallback-content">
                <h2>Map Preview</h2>
                <p>Map data is loading...</p>
                <p>If this persists, check your internet connection.</p>
            </div>
        </body>
        </html>
        """
        self.map_widget.setHtml(fallback_html)
