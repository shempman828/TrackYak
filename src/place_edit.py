from geopy import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from src.place_search_dialog import SearchResultsDialog
from src.logger_config import logger
from src.wikipedia_seach import search_wikipedia


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
        self.parent_edit = QLineEdit()
        self.region_edit = QLineEdit()

        # Add search buttons
        search_layout = QHBoxLayout()
        self.search_coord_button = QPushButton("Search Coordinates")
        self.search_wiki_button = QPushButton("Search Wikipedia")
        self.search_coord_button.clicked.connect(self.search_coordinates)
        self.search_wiki_button.clicked.connect(self.search_wikipedia)
        search_layout.addWidget(self.search_coord_button)
        search_layout.addWidget(self.search_wiki_button)

        # Setup autocompletion for parent_edit
        places = self.controller.get.get_all_entities("Place")
        place_names = [p.place_name for p in places]
        self.parent_completer = QCompleter(place_names)
        self.parent_edit.setCompleter(self.parent_completer)

        # Populate if editing
        if self.place:
            self.name_edit.setText(self.place.place_name)
            self.type_edit.setText(self.place.place_type)
            self.lat_edit.setText(str(self.place.place_latitude))
            self.lon_edit.setText(str(self.place.place_longitude))
            self.desc_edit.setText(self.place.place_description)
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

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def search_wikipedia(self):
        """Search Wikipedia for place information and populate description."""
        place_name = self.name_edit.text().strip()
        region = self.region_edit.text().strip()

        if not place_name:
            QMessageBox.warning(
                self, "Search Error", "Please enter a place name to search Wikipedia."
            )
            return

        try:
            # Build search query
            search_query = f"{place_name}"
            if region:
                search_query += f", {region}"

            # Show Wikipedia search dialog
            title, summary, full_content, link, images = search_wikipedia(
                search_query, self
            )

            if title and summary:
                # Populate the description field with Wikipedia summary
                current_desc = self.desc_edit.text().strip()
                new_desc = (
                    f"{current_desc}\n\nWikipedia: {summary[:500]}..."
                    if current_desc
                    else f"Wikipedia: {summary[:500]}..."
                )
                self.desc_edit.setText(new_desc)

                # Show success message
                QMessageBox.information(
                    self,
                    "Wikipedia Search",
                    f"Found Wikipedia article: {title}\n\nFirst 500 characters of summary added to description.",
                )

        except Exception as e:
            logger.error(f"Wikipedia search failed: {str(e)}", exc_info=True)
            QMessageBox.critical(
                self, "Wikipedia Error", f"Failed to search Wikipedia: {str(e)}"
            )

    def search_coordinates(self):
        """Fetch latitude and longitude using the place name and region."""
        place_name = self.name_edit.text().strip()
        region = self.region_edit.text().strip()

        if not place_name:
            QMessageBox.warning(
                self, "Search Error", "Please enter a place name to search."
            )
            return

        try:
            query = f"{place_name}, {region}" if region else place_name
            locations = self.geolocator.geocode(
                query, exactly_one=False, limit=5
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
                QMessageBox.warning(
                    self,
                    "Search Error",
                    "No coordinates found for the given place name.",
                )
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            QMessageBox.critical(
                self, "Search Error", f"Failed to fetch coordinates: {str(e)}"
            )

    def get_place_data(self):
        """Return form data as dictionary."""
        parent_name = self.parent_edit.text().strip()
        parent_id = None

        if parent_name:
            # Look up the parent_id from the parent place name
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
        }

    def validate_and_accept(self):
        """Validate form data and accept the dialog if valid."""
        place_data = self.get_place_data()
        if place_data is None:
            return  # Validation failed, do not close the dialog

        # Validate latitude and longitude (if provided)
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

        self.accept()
