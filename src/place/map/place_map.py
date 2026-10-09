import html as html_escape
import json
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QSettings, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.segmented_control import SegmentedControl
from src.foundation.asset_paths import asset
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.place.map.place_map_filter import MultiSelectWidget
from src.place.place_assoc_details import AssociationDetailsDialog
from src.place.place_types import merge_type_selection, order_types_by_hierarchy, type_color, type_label

# QSettings key for remembering the selected place type filters
_SETTINGS_SELECTED_TYPES = "place_map/selected_types"

# QSettings key for remembering the marker clustering aggressiveness
_SETTINGS_CLUSTER_LEVEL = "place_map/cluster_level"

# The legend lists the most common types on the map; the rest fold into "+N more".
_LEGEND_MAX_TYPES = 10

# Injected once per page load: a legend control in the bottom-left corner and
# the function that refreshes it, plus the marker registry used by focus.
_LEGEND_SETUP_JS = """
if (!window.placeLegend) {
    var style = document.createElement('style');
    style.textContent = `
        .place-legend { background: rgba(17, 18, 26, 0.88); color: #b8c0f0;
            border: 1px solid rgba(133, 153, 234, 0.35); border-radius: 8px;
            padding: 8px 10px; font: 12px Cambria, Georgia, serif; line-height: 1.6; }
        .place-legend .dot { display: inline-block; width: 9px; height: 9px;
            border-radius: 50%; margin-right: 6px; border: 1px solid rgba(255,255,255,0.6); }
        .place-legend .count { color: #7a82a8; margin-left: 4px; }
        .place-legend .more { color: #7a82a8; font-style: italic; }
        .place-marker-focus { box-shadow: 0 0 0 4px rgba(234, 214, 133, 0.85) !important; }
    `;
    document.head.appendChild(style);
    window.placeLegend = L.control({ position: 'bottomleft' });
    window.placeLegend.onAdd = function () { return L.DomUtil.create('div', 'place-legend'); };
    window.placeLegend.addTo(map);
    window.setPlaceLegend = function (html) {
        var el = window.placeLegend.getContainer();
        el.innerHTML = html;
        el.style.display = html ? '' : 'none';
    };
}
"""


class MapView(QWidget):
    """Map tab: a full-size Leaflet map with one marker per place, colored by type."""

    show_in_list_requested = Signal(int)  # place_id from a marker popup
    show_unmapped_requested = Signal()  # the "N places not on map" link

    # How aggressively nearby markers snap together into a single cluster.
    # "radius" is Leaflet.markercluster's maxClusterRadius (pixels around a
    # cluster center that will absorb other markers); "disable_zoom" is the
    # zoom level past which clustering stops entirely. A radius of 0 disables
    # clustering outright.
    CLUSTER_LEVELS: ClassVar[dict[str, dict[str, int]]] = {
        "Off": {"radius": 0, "disable_zoom": 20},
        "Low": {"radius": 15, "disable_zoom": 16},
        "Medium": {"radius": 25, "disable_zoom": 15},
        "High": {"radius": 60, "disable_zoom": 13},
    }
    DEFAULT_CLUSTER_LEVEL = "Medium"

    def __init__(self, controller, autoload=True):
        super().__init__()
        self.controller = controller
        self.selected_types = set()  # Track selected types
        self.all_place_types = set()  # Track all available types
        self._settings = QSettings()
        self._map_initialized = False
        self._page_ready = False
        self._pending_js = []
        self._mapped_place_ids = set()
        self.cluster_level = self._load_saved_cluster_level()
        self.init_ui()
        self.setup_js_communication()
        if autoload:
            self.refresh_place_types()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Filter bar: one always-visible line
        bar = QWidget()
        bar.setObjectName("PlaceFilterBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)

        self.multi_select_widget = MultiSelectWidget()
        self.multi_select_widget.selection_changed.connect(self.apply_filter)
        bar_layout.addWidget(self.multi_select_widget)

        self.unmapped_button = QPushButton()
        self.unmapped_button.setProperty("linkButton", True)
        self.unmapped_button.setToolTip("Open these places in the list so you can add coordinates")
        self.unmapped_button.clicked.connect(self.show_unmapped_requested)
        self.unmapped_button.hide()
        bar_layout.addWidget(self.unmapped_button)
        bar_layout.addStretch()

        stacking_label = QLabel("Stacking")
        stacking_label.setProperty("textRole", "muted")
        bar_layout.addWidget(stacking_label)
        self.cluster_control = SegmentedControl(list(self.CLUSTER_LEVELS))
        self.cluster_control.setToolTip("How strongly nearby markers group together")
        self.cluster_control.setCurrentText(self.cluster_level)
        self.cluster_control.currentTextChanged.connect(self.apply_cluster_level)
        bar_layout.addWidget(self.cluster_control)
        layout.addWidget(bar)

        self.map_widget = QWebEngineView()
        self.map_widget.setObjectName("PlaceMap")
        self.map_widget.loadFinished.connect(self._on_page_loaded)
        layout.addWidget(self.map_widget, 1)

    def _on_page_loaded(self, ok):
        self._page_ready = ok
        pending = self._pending_js
        self._pending_js = []
        for code in pending:
            self.map_widget.page().runJavaScript(code)

    def _run_js(self, code):
        if self._page_ready:
            self.map_widget.page().runJavaScript(code)
        else:
            self._pending_js.append(code)

    def refresh_place_types(self, places=None):
        """Reload the type filter options from the data, then redraw the map.
        `places` lets a caller that already fetched them skip a query."""
        try:
            if places is None:
                places = self.controller.get.get_all_entities("Place")
            unique_types = {type_label(p.place_type) for p in places}

            restored = merge_type_selection(self.all_place_types, self.selected_types, unique_types, initial=self._load_saved_selected_types())
            self.all_place_types = unique_types
            self.multi_select_widget.blockSignals(True)
            try:
                self.multi_select_widget.set_items(order_types_by_hierarchy(places, unique_types), default_selected=False)
                self.multi_select_widget.set_selected_items(restored)
            finally:
                self.multi_select_widget.blockSignals(False)
            self.selected_types = set(self.multi_select_widget.get_selected_items())

            logger.info(f"Refreshed place types: {len(unique_types)} unique types found, {len(self.selected_types)} selected")
            self.load_places([self._create_place_data(p) for p in places])

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error refreshing place types: {e!s}")
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
                        self.map_view.show_associations_for_place(data.get("placeId"))
                    elif data.get("type") == "showInList":
                        self.map_view.show_in_list_requested.emit(int(data.get("placeId")))
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.error(f"Error handling JS message: {e!s}")

        self.bridge = Bridge(self)
        self.channel = QWebChannel()
        self.map_widget.page().setWebChannel(self.channel)
        self.channel.registerObject("pyBridge", self.bridge)

    def apply_filter(self, selected_types):
        """Apply filter to map markers based on selected types."""
        self.selected_types = set(selected_types)
        self._save_selected_types(self.selected_types)
        self.load_places()

    def apply_cluster_level(self, level_name):
        """Handle the user picking a new marker-stacking aggressiveness level."""
        if level_name not in self.CLUSTER_LEVELS:
            return
        self.cluster_level = level_name
        self._settings.setValue(_SETTINGS_CLUSTER_LEVEL, level_name)
        self._apply_cluster_options_to_map()

    def _apply_cluster_options_to_map(self):
        """Recreate the marker cluster group in-page with the current level's
        options, then repopulate it. Leaflet.markercluster options like
        maxClusterRadius can't be changed on an existing group, so the group
        itself has to be swapped out rather than reconfigured in place."""
        if not self._map_initialized:
            return
        opts = self.CLUSTER_LEVELS[self.cluster_level]
        js = f"""
            if (typeof markerClusterGroup !== 'undefined') {{
                map.removeLayer(markerClusterGroup);
            }}
            markerClusterGroup = L.markerClusterGroup({{
                maxClusterRadius: {opts["radius"]},
                disableClusteringAtZoom: {opts["disable_zoom"]},
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false
            }});
            map.addLayer(markerClusterGroup);
        """
        self._run_js(js)
        self.load_places()

    def _load_saved_cluster_level(self):
        """Return the previously saved clustering aggressiveness, or the default."""
        saved = self._settings.value(_SETTINGS_CLUSTER_LEVEL, self.DEFAULT_CLUSTER_LEVEL, type=str)
        if saved not in self.CLUSTER_LEVELS:
            return self.DEFAULT_CLUSTER_LEVEL
        return saved

    def _load_saved_selected_types(self):
        """Return the previously saved set of selected types, or None if unset."""
        saved = self._settings.value(_SETTINGS_SELECTED_TYPES, None, type=str)
        if not saved:
            return None
        try:
            return set(json.loads(saved))
        except (ValueError, TypeError):
            return None

    def _save_selected_types(self, selected_types):
        """Persist the selected place types so the filter survives restarts."""
        self._settings.setValue(_SETTINGS_SELECTED_TYPES, json.dumps(sorted(selected_types)))

    def load_places(self, places: list[dict] | None = None):
        try:
            if places is None:
                raw_places = self.controller.get.get_all_entities("Place")
                places = [self._create_place_data(p) for p in raw_places]

            filtered_places = [p for p in places if p["type_label"] in self.selected_types]
            unmapped = sum(1 for p in filtered_places if p["lat"] is None or p["lon"] is None)
            self.unmapped_button.setVisible(unmapped > 0)
            self.unmapped_button.setText(f"{unmapped} place{'s' if unmapped != 1 else ''} not on map →")

            self.generate_map(filtered_places)

        except SQLAlchemyError as e:
            logger.error(f"Failed to load places: {e!s}", exc_info=True)

    def generate_map(self, places: list[dict]):
        """Create and display Leaflet map with color-coded markers."""
        try:
            valid_places = [p for p in places if p["lat"] is not None and p["lon"] is not None]
            self._mapped_place_ids = {p["id"] for p in valid_places}

            if valid_places:
                avg_lat = sum(p["lat"] for p in valid_places) / len(valid_places)
                avg_lon = sum(p["lon"] for p in valid_places) / len(valid_places)
                zoom_level = 4
            else:
                avg_lat, avg_lon, zoom_level = 30, 0, 2

            if not self._map_initialized:
                # Only the very first render needs a full document load
                # (Leaflet/map/tile-layer init). Later refreshes (filter
                # changes, place add/edit/delete, tab revisits) push an
                # incremental marker update instead -- a full setHtml()
                # reload tears down and rebuilds the whole Chromium page,
                # which is what caused the visible whole-screen flash.
                html_content = self._create_map_html(valid_places, avg_lat, avg_lon, zoom_level)
                self._page_ready = False
                self.map_widget.setHtml(html_content)
                self._map_initialized = True
                self._run_js(_LEGEND_SETUP_JS)
            else:
                markers_js = self._create_markers_js(valid_places)
                bounds_js = self._create_bounds_js(valid_places)
                self._run_js(f"markerClusterGroup.clearLayers();\nwindow.placeMarkers = {{}};\n{markers_js}\n{bounds_js}")
            self._run_js(f"window.setPlaceLegend && window.setPlaceLegend({json.dumps(self._legend_html(valid_places))});")

            logger.info(f"Map generated with {len(valid_places)} valid places (filter: {len(self.selected_types)} types selected)")

        except (KeyError, RuntimeError) as e:
            logger.error(f"Map generation failed: {e!s}", exc_info=True)
            self.show_fallback_map()

    def _legend_html(self, places: list[dict]) -> str:
        """Legend rows for the types currently on the map, most common first."""
        counts = {}
        for place in places:
            counts[place["type_label"]] = counts.get(place["type_label"], 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        rows = [
            f"<div><span class='dot' style='background:{type_color(label)}'></span>{html_escape.escape(label)}<span class='count'>{count}</span></div>"
            for label, count in ordered[:_LEGEND_MAX_TYPES]
        ]
        if len(ordered) > _LEGEND_MAX_TYPES:
            rows.append(f"<div class='more'>+{len(ordered) - _LEGEND_MAX_TYPES} more types</div>")
        return "".join(rows)

    def focus_place(self, place_id) -> bool:
        """Zoom to a place's marker and open its popup. Returns False (and
        tells the user why) when the place has no marker on the map."""
        if place_id not in self._mapped_place_ids:
            show_status_message(self, "This place is not on the map. Its type is hidden by the map's type filter, or it has no coordinates.")
            return False
        self._run_js(f"""
            (function () {{
                var m = window.placeMarkers && window.placeMarkers[{int(place_id)}];
                if (!m) return;
                markerClusterGroup.zoomToShowLayer(m, function () {{ m.openPopup(); }});
            }})();
        """)
        return True

    def _create_map_html(
        self, places: list[dict], center_lat: float, center_lon: float, zoom: int
    ) -> str:
        """Create complete HTML content for the map with WebChannel support."""

        # Load HTML template from file
        template_path = Path(asset("place_map_template.html"))
        if template_path.exists():
            try:
                with template_path.open(encoding="utf-8") as f:
                    template = f.read()
            except OSError as e:
                logger.error(f"Failed to load HTML template: {e!s}")
                template = self._get_fallback_template()
        else:
            logger.warning("HTML template not found, using fallback")
            template = self._get_fallback_template()

        # Create markers JavaScript code
        markers_js = self._create_markers_js(places)

        # Generate bounds JavaScript
        bounds_js = self._create_bounds_js(places)

        cluster_opts = self.CLUSTER_LEVELS[self.cluster_level]

        # Use .replace() instead of .format() to avoid CSS brace conflicts
        html = template.replace("{center_lat}", str(center_lat))
        html = html.replace("{center_lon}", str(center_lon))
        html = html.replace("{zoom}", str(zoom))
        html = html.replace("{markers_js}", markers_js)
        html = html.replace("{bounds_js}", bounds_js)
        html = html.replace("{cluster_radius}", str(cluster_opts["radius"]))
        return html.replace("{cluster_disable_zoom}", str(cluster_opts["disable_zoom"]))

    def _create_markers_js(self, places: list[dict]) -> str:
        """JavaScript that adds one type-colored marker per place and registers
        it in window.placeMarkers (by place id) so focus_place can find it."""
        marker_chunks = ["window.placeMarkers = window.placeMarkers || {};\n"]
        for place in places:
            marker_color = type_color(place["type_label"])
            popup_content = json.dumps(self._create_popup_content(place))
            tooltip_name = json.dumps(place["name"] or "")
            marker_html = json.dumps(
                f'<div style="background-color: {marker_color}; width: 18px; height: 18px; border-radius: 50%; '
                'border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div>'
            )
            marker_chunks.append(f"""
                (function () {{
                    var m = L.marker([{place["lat"]}, {place["lon"]}], {{
                        icon: L.divIcon({{ className: 'custom-div-icon', html: {marker_html}, iconSize: [18, 18], iconAnchor: [9, 9] }})
                    }}).bindPopup({popup_content}).bindTooltip({tooltip_name});
                    window.placeMarkers[{int(place["id"])}] = m;
                    markerClusterGroup.addLayer(m);
                }})();
                """)
        return "".join(marker_chunks)

    def _create_bounds_js(self, places: list[dict]) -> str:
        """Create JavaScript code for map bounds."""
        if not places:
            return ""

        bounds_chunks = ["var bounds = L.latLngBounds([\n"]
        for place in places:
            bounds_chunks.append(f"    [{place['lat']}, {place['lon']}],\n")
        bounds_chunks.append("]);\nmap.fitBounds(bounds, { padding: [20, 20] });")
        return "".join(bounds_chunks)

    def _get_fallback_template(self) -> str:
        """Get a fallback HTML template if file is not found."""
        unpkg = "https://unpkg.com"
        esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
        cluster = f"{unpkg}/leaflet.markercluster@1.5.3/dist"
        base_tiles = f"{esri}/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}"
        ref_tiles = f"{esri}/World_Dark_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}"
        return f"""<!DOCTYPE html>
<html>
<head>
    <title>Places Map</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="{unpkg}/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin=""/>
    <script src="{unpkg}/leaflet@1.9.4/dist/leaflet.js"
            integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
            crossorigin=""></script>
    <link rel="stylesheet" href="{cluster}/MarkerCluster.css" />
    <link rel="stylesheet" href="{cluster}/MarkerCluster.Default.css" />
    <script src="{cluster}/leaflet.markercluster.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ height: 100vh; width: 100%; }}
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
        window.showInList = function(placeId) {{
            if (window.pyBridge) {{
                window.pyBridge.handle_js_message(JSON.stringify({{
                    type: 'showInList',
                    placeId: placeId
                }}));
            }}
        }};
        var map = L.map('map', {{ worldCopyJump: true }})
            .setView([{{center_lat}}, {{center_lon}}], {{zoom}});
        // Esri "Dark Gray Canvas" -- keyless dark raster basemap (label-free
        // base + transparent reference overlay). Native tiles stop at z16.
        L.tileLayer('{base_tiles}', {{
            attribution: 'Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors',
            maxZoom: 18,
            maxNativeZoom: 16
        }}).addTo(map);
        L.tileLayer('{ref_tiles}', {{
            maxZoom: 18,
            maxNativeZoom: 16,
            zIndex: 2
        }}).addTo(map);
        var markerClusterGroup = L.markerClusterGroup({{
            maxClusterRadius: {{cluster_radius}},
            disableClusteringAtZoom: {{cluster_disable_zoom}},
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false
        }});
        {{markers_js}}
        map.addLayer(markerClusterGroup);
        {{bounds_js}}
    </script>
</body>
</html>"""

    def _create_popup_content(self, place: dict) -> str:
        """Popup HTML: name, type, coordinates, a description excerpt, and actions."""
        name = html_escape.escape(place["name"] or "")
        label = html_escape.escape(place["type_label"])
        content = [
            f"<h3 style='margin: 0 0 8px 0; color: #8599ea;'>{name}</h3>",
            "<div style='border-bottom: 1px solid rgba(133, 153, 234, 0.3); padding-bottom: 8px; margin-bottom: 8px;'>",
            f"<strong>Type:</strong> {label}<br>",
            f"<strong>Coordinates:</strong> {place['lat']:.4f}, {place['lon']:.4f}",
            "</div>",
        ]
        description = place.get("description") or ""
        if description:
            ellipsis = "…" if len(description) > 200 else ""
            content.append(f"<div style='margin-top: 8px;'>{html_escape.escape(description[:200])}{ellipsis}</div>")

        place_id = int(place["id"])
        button_style = "border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; margin: 0 3px;"
        content.append(
            f"<div style='margin-top: 12px; text-align: center;'>"
            f"<button onclick='viewAssociations({place_id})' style='background-color: #8599ea; color: #0b0c10; {button_style}'>View Associations</button>"
            f"<button onclick='showInList({place_id})' style='background-color: #1a1b26; color: #b8c0f0; border: 1px solid #8599ea; {button_style}'>Show in List</button>"
            "</div>"
        )
        return "".join(content)

    def show_associations_for_place(self, place_id):
        """Show associations for a place from map pin click."""
        try:
            place = self.controller.get.get_entity_object("Place", place_id=int(place_id))
            if place:
                dialog = AssociationDetailsDialog(self.controller, place, self)
                dialog.exec_()
            else:
                logger.error(f"Place with ID {place_id} not found")
                show_status_message(self, f"Place with ID {place_id} not found")
        except (SQLAlchemyError, ValueError, TypeError, RuntimeError) as e:
            logger.error(f"Error showing associations: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to show associations: {e!s}")

    def _create_place_data(self, raw_place) -> dict:
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
            "type_label": type_label(raw_place.place_type),
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
        self._map_initialized = False
        self._page_ready = False
        self.map_widget.setHtml(fallback_html)
