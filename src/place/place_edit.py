from geopy import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from src.common.entity_completer_edit import EntityCompleterEdit
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.place.place_search_dialog import SearchResultsDialog


class PlaceEditDialog(QDialog):
    """Form dialog for creating/editing places."""

    def __init__(self, controller, parent=None, place=None):
        super().__init__(parent)  # Pass the parent to QDialog
        self.controller = controller  # Store the controller
        self.place = place
        self.geolocator = Nominatim(user_agent="place_manager")
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Edit Place" if self.place else "Add Place")
        layout = QFormLayout(self)

        # Form fields
        self.name_edit = QLineEdit()
        self.type_edit = QLineEdit()
        self.lat_edit = QLineEdit()
        self.lon_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        self.parent_edit = EntityCompleterEdit("Search places…")
        self.region_edit = QLineEdit()
        self.mbid_edit = QLineEdit()
        self.mbid_edit.setPlaceholderText("MusicBrainz ID")

        # Add search buttons
        search_layout = QHBoxLayout()
        self.search_coord_button = QPushButton("Search Coordinates")
        self.search_coord_button.clicked.connect(self.search_coordinates)
        search_layout.addWidget(self.search_coord_button)

        # Setup autocompletion for parent_edit. Keying on place_name locks
        # in the selected place_id when a completion is picked, so save-time
        # doesn't have to re-resolve the parent by a name that could collide
        # with another place or no longer exist.
        places = self.controller.get.get_all_entities("Place")
        parent_index = {p.place_name: p.place_id for p in places if p.place_name}
        self.parent_edit.set_index(parent_index)
        # EntityCompleterEdit claims Enter/Return for its own use (see its
        # docstring) instead of letting it reach the dialog's default
        # button -- reconnect it here so Enter still submits this form like
        # every other field does via QDialogButtonBox.
        self.parent_edit.returnPressed.connect(self.validate_and_accept)

        # Populate if editing
        if self.place:
            self.name_edit.setText(self.place.place_name)
            self.type_edit.setText(self.place.place_type)
            if self.place.place_latitude is not None:
                self.lat_edit.setText(str(self.place.place_latitude))
            if self.place.place_longitude is not None:
                self.lon_edit.setText(str(self.place.place_longitude))
            self.desc_edit.setText(self.place.place_description)
            self.mbid_edit.setText(self.place.MBID or "")
            place = self.controller.get.get_entity_object(
                "Place", place_id=self.place.parent_id
            )
            place_name = place.place_name if place else ""
            self.parent_edit.setText(place_name)

        # Add rows
        layout.addRow("Name:", self.name_edit)
        layout.addRow("Type:", self.type_edit)
        layout.addRow("Latitude:", self.lat_edit)
        layout.addRow("Longitude:", self.lon_edit)
        layout.addRow("Region/Country:", self.region_edit)
        layout.addRow("Search Tools:", search_layout)  # Combined search buttons
        layout.addRow("Description:", self.desc_edit)
        layout.addRow("Parent Place:", self.parent_edit)
        layout.addRow("MBID:", self.mbid_edit)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def search_coordinates(self):
        """Fetch latitude and longitude using the place name and region."""

        place_name = self.name_edit.text().strip()
        region = self.region_edit.text().strip()
        logger.debug(f"Searching for place {place_name} in region {region}")

        if not place_name:
            show_status_message(self, "Please enter a place name to search.")
            return

        try:
            query = f"{place_name}, {region}" if region else place_name
            locations = self.geolocator.geocode(
                query, exactly_one=False, limit=5, language="en"
            )  # Get up to 5 results

            if locations:
                if len(locations) > 1:
                    # Show a dialog to let the user choose the correct result
                    dialog = SearchResultsDialog(locations, self)
                    if dialog.exec_() == QDialog.Accepted:
                        selected_location = dialog.get_selected_result()
                        if selected_location:
                            self.lat_edit.setText(str(selected_location.latitude))
                            self.lon_edit.setText(str(selected_location.longitude))
                else:
                    # Only one result, use it directly
                    self.lat_edit.setText(str(locations[0].latitude))
                    self.lon_edit.setText(str(locations[0].longitude))
            else:
                show_status_message(
                    self, "No coordinates found for the given place name."
                )
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            QMessageBox.critical(
                self, "Search Error", f"Failed to fetch coordinates: {e!s}"
            )

    def get_place_data(self):
        """Return form data as dictionary."""
        parent_name = self.parent_edit.text().strip()
        parent_id = None

        if parent_name:
            # Prefer the id locked in when the user picked a completion --
            # avoids re-resolving by name (ambiguous with same-named places)
            # or missing a place created after this dialog's index was
            # built. Falls back to a name lookup for the edit-mode prefill,
            # which sets the text directly and never locks an id.
            parent_id = self.parent_edit.matched_id()
            if parent_id is None:
                parent_object = self.controller.get.get_entity_object(
                    "Place", place_name=parent_name
                )
                parent_id = parent_object.place_id if parent_object else None
            if not parent_id:
                QMessageBox.warning(
                    self, "Invalid Parent", f"Parent place '{parent_name}' not found."
                )
                return None  # Return None to indicate validation failure

        return {
            "place_name": self.name_edit.text().strip(),
            "place_type": self.type_edit.text().strip(),
            "place_latitude": float(self.lat_edit.text())
            if self.lat_edit.text().strip()
            else None,
            "place_longitude": float(self.lon_edit.text())
            if self.lon_edit.text().strip()
            else None,
            "place_description": self.desc_edit.text().strip(),
            "parent_id": parent_id,  # Use the looked-up parent_id
            "MBID": self.mbid_edit.text().strip() or None,
        }

    def validate_and_accept(self):
        """Validate form data and accept the dialog if valid."""
        # Validate latitude and longitude (if provided) before get_place_data()
        # converts them with an unguarded float(), which would otherwise raise
        # an uncaught ValueError on non-numeric text (e.g. a stale "None").
        lat_text = self.lat_edit.text().strip()
        lon_text = self.lon_edit.text().strip()
        if lat_text or lon_text:
            try:
                if lat_text:
                    float(lat_text)  # Validate latitude
                if lon_text:
                    float(lon_text)  # Validate longitude
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Invalid Coordinates",
                    "Latitude and Longitude must be numbers.",
                )
                return

        place_data = self.get_place_data()
        if place_data is None:
            return  # Validation failed, do not close the dialog

        self.accept()
