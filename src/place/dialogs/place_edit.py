"""Place edit dialog: add, edit, or bulk-edit places, with inline geocoding and validation."""

import math

from geopy import Nominatim
from geopy.exc import GeopyError
from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout

from src.common.cancellable_worker import CancellableWorker
from src.common.widgets.detail_card import DetailCard
from src.common.widgets.entity_completer_context import place_context_map
from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.logger_config import logger
from src.place.place_hierarchy import would_create_cycle

# Geocoder results to offer when a name is ambiguous.
_GEOCODE_LIMIT = 5
_GEOCODE_TIMEOUT_S = 10
# Nominatim's usage policy asks for a user agent that names the application.
_GEOCODE_USER_AGENT = "YakTrack"

_CYCLE_ERROR = "A place cannot be inside itself or one of its own child places."

# Geocode workers still running after their dialog closed; kept alive until they finish.
_running_geocodes: set = set()


class _GeocodeWorker(CancellableWorker):
    """Look up candidate locations for a query off the GUI thread."""

    found = Signal(list)
    failed = Signal(str)

    def __init__(self, query):
        super().__init__()
        self._query = query

    def run(self):
        """Geocode the query; emit found(locations) or failed(message)."""
        try:
            geolocator = Nominatim(user_agent=_GEOCODE_USER_AGENT, timeout=_GEOCODE_TIMEOUT_S)
            locations = geolocator.geocode(self._query, exactly_one=False, limit=_GEOCODE_LIMIT, language="en")
        except GeopyError as e:
            logger.warning(f"Coordinate search failed for {self._query!r}: {e}")
            if not self.is_cancelled:
                self.failed.emit("The coordinate search failed. Check the internet connection and try again.")
            return
        if not self.is_cancelled:
            self.found.emit(list(locations or []))


def _parse_coordinate(text, limit):
    """Return (value, ok) for a coordinate field; an empty field is (None, True)."""
    text = text.strip()
    if not text:
        return None, True
    try:
        value = float(text)
    except ValueError:
        return None, False
    return value, math.isfinite(value) and -limit <= value <= limit


class PlaceEditDialog(QDialog):
    """Form to add or edit one place, or bulk-edit several when `place` is a list."""

    def __init__(self, controller, parent=None, place=None, *, preset_parent=None, new_parent_of=None):
        super().__init__(parent)
        self.controller = controller
        self.places = place if isinstance(place, list) else ([place] if place else [])
        self.is_multi = len(self.places) > 1
        # Only meaningful when is_multi is False.
        self.place = self.places[0] if self.places else None
        # preset_parent: Add mode with the Parent field filled in (New Child Place).
        # new_parent_of: Add mode for a place that becomes the parent of this one (New Parent Place).
        self._preset_parent = preset_parent
        self._new_parent_of = new_parent_of
        # Multi mode: only the fields the user touched are written on save.
        self._dirty: set = set()
        # (text, place_id) put into the Parent field by the dialog itself, so
        # saving keeps that exact place even when another place has the same name.
        self._prefill_parent: tuple | None = None
        self._geocode_worker = None
        self.init_ui()

    def init_ui(self):
        """Build the Identity, Location, and Notes cards and fill them from the place(s)."""
        if self.is_multi:
            self.setWindowTitle(f"Edit {len(self.places)} Places")
        else:
            self.setWindowTitle("Edit Place" if self.place else "Add Place")
        self.setObjectName("PlaceEditDialog")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        if self.is_multi:
            note = QLabel(f"Changes apply to all {len(self.places)} selected places. Fields you do not change keep their current values.")
            note.setProperty("textRole", "note")
            note.setWordWrap(True)
            layout.addWidget(note)

        # Name, MBID, and the coordinate search identify one real-world place,
        # so multi-edit mode leaves them out (docs/specs/place-list-multi-edit.md).
        if not self.is_multi:
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("e.g. Nashville")
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("e.g. City, Studio, Country")
        self.lat_edit = QLineEdit()
        self.lat_edit.setPlaceholderText("Latitude (-90 to 90)")
        self.lon_edit = QLineEdit()
        self.lon_edit.setPlaceholderText("Longitude (-180 to 180)")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Notes about this place")
        self.desc_edit.setTabChangesFocus(True)
        self.desc_edit.setMinimumHeight(90)
        self.parent_edit = EntityCompleterEdit("Search places…")
        if not self.is_multi:
            self.region_edit = QLineEdit()
            self.region_edit.setPlaceholderText("Region or country, to narrow the search")
            self.mbid_edit = QLineEdit()
            self.mbid_edit.setPlaceholderText("MusicBrainz ID")
            self.search_coord_button = QPushButton("Find Coordinates")
            self.search_coord_button.setToolTip("Look up the coordinates from the name and region")
            self.search_coord_button.clicked.connect(self.search_coordinates)

        # Keying the completer on place_name locks in the picked place_id, so
        # saving never has to re-resolve the parent by a name that can collide.
        places = self.controller.get.get_all_entities("Place")
        self._places_by_id = {p.place_id: p for p in places}
        parent_index = {p.place_name: p.place_id for p in places if p.place_name}
        self.parent_edit.set_index(parent_index, place_context_map(places))

        # Suggest existing types without restricting entry to them.
        known_types = sorted({p.place_type.strip().title() for p in places if p.place_type and p.place_type.strip()})
        type_model = QStringListModel(known_types, self.type_edit)
        type_completer = QCompleter(type_model, self.type_edit)
        type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        type_completer.setFilterMode(Qt.MatchContains)
        self.type_edit.setCompleter(type_completer)
        # EntityCompleterEdit keeps Enter for itself; reconnect it so Enter still submits the form.
        self.parent_edit.returnPressed.connect(self.validate_and_accept)

        # Inline validation messages, shown under the field they refer to.
        self.name_error = self._error_label()
        self.coords_error = self._error_label()
        self.parent_error = self._error_label()
        self.parent_edit.textEdited.connect(lambda _t: self.parent_error.hide())
        self.lat_edit.textEdited.connect(lambda _t: self.coords_error.hide())
        self.lon_edit.textEdited.connect(lambda _t: self.coords_error.hide())

        # ── Identity ──
        identity = DetailCard("Identity")
        identity_form = self._form()
        if not self.is_multi:
            identity_form.addRow("Name", self.name_edit)
            identity_form.addRow("", self.name_error)
            self.name_edit.textEdited.connect(lambda _t: self.name_error.hide())
        identity_form.addRow("Type", self.type_edit)
        identity_form.addRow("Parent", self.parent_edit)
        identity_form.addRow("", self.parent_error)
        identity.body.addLayout(identity_form)
        layout.addWidget(identity)

        # ── Location ──
        location = DetailCard("Location")
        location_form = self._form()
        if not self.is_multi:
            search_row = QHBoxLayout()
            search_row.addWidget(self.region_edit, 1)
            search_row.addWidget(self.search_coord_button)
            location_form.addRow("Search", search_row)
            self.search_status = QLabel()
            self.search_status.setProperty("textRole", "muted")
            self.search_status.setWordWrap(True)
            self.search_status.hide()
            location_form.addRow("", self.search_status)
            self.results_list = QListWidget()
            self.results_list.setObjectName("GeocodeResults")
            self.results_list.setToolTip("Click a result to use its coordinates")
            self.results_list.itemClicked.connect(self._use_result)
            self.results_list.hide()
            location_form.addRow("", self.results_list)
        coords_row = QHBoxLayout()
        coords_row.addWidget(self.lat_edit)
        coords_row.addWidget(self.lon_edit)
        location_form.addRow("Coordinates", coords_row)
        location_form.addRow("", self.coords_error)
        location.body.addLayout(location_form)
        layout.addWidget(location)

        # ── Notes & links ──
        notes = DetailCard("Notes & Links")
        notes_form = self._form()
        notes_form.addRow("Description", self.desc_edit)
        if not self.is_multi:
            notes_form.addRow("MBID", self.mbid_edit)
        notes.body.addLayout(notes_form)
        layout.addWidget(notes)

        if self.is_multi:
            self._populate_multi()
            # textEdited (unlike textChanged) never fires for the setText()
            # prefill above, so untouched fields stay out of self._dirty.
            # QPlainTextEdit has no textEdited, so its textChanged is connected
            # only after the prefill; picked covers a completer pick.
            self.type_edit.textEdited.connect(lambda _t: self._mark_dirty("place_type"))
            self.lat_edit.textEdited.connect(lambda _t: self._mark_dirty("place_latitude"))
            self.lon_edit.textEdited.connect(lambda _t: self._mark_dirty("place_longitude"))
            self.desc_edit.textChanged.connect(lambda: self._mark_dirty("place_description"))
            self.parent_edit.textEdited.connect(lambda _t: self._mark_dirty("parent_id"))
            self.parent_edit.picked.connect(lambda: self._mark_dirty("parent_id"))
        elif self.place:
            self.name_edit.setText(self.place.place_name)
            self.type_edit.setText(self.place.place_type)
            if self.place.place_latitude is not None:
                self.lat_edit.setText(str(self.place.place_latitude))
            if self.place.place_longitude is not None:
                self.lon_edit.setText(str(self.place.place_longitude))
            self.desc_edit.setPlainText(self.place.place_description or "")
            self.mbid_edit.setText(self.place.MBID or "")
            self._prefill_parent_field(self._place_by_id(self.place.parent_id))
        elif self._preset_parent is not None:
            self._prefill_parent_field(self._preset_parent)
        elif self._new_parent_of is not None:
            # The new place takes over the old parent slot by default.
            self._prefill_parent_field(self._place_by_id(self._new_parent_of.parent_id))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _form():
        """Form layout with the dialog's label alignment and spacing."""
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        return form

    @staticmethod
    def _error_label():
        """Hidden inline error label."""
        label = QLabel()
        label.setProperty("textRole", "error")
        label.setWordWrap(True)
        label.hide()
        return label

    def _show_error(self, label, text):
        """Show an inline error and grow the dialog to fit it."""
        label.setText(text)
        label.show()
        self._grow_to_fit()

    def _grow_to_fit(self):
        """Grow the dialog when an inline message or result list appears, so rows do not overlap."""

        def grow():
            needed = self.layout().totalHeightForWidth(self.width()) if self.layout().hasHeightForWidth() else self.sizeHint().height()
            if needed > self.height():
                self.resize(self.width(), needed)

        QTimer.singleShot(0, grow)

    def _place_by_id(self, place_id):
        """Place for `place_id` from the loaded places, else from the database."""
        if place_id is None:
            return None
        place = self._places_by_id.get(place_id)
        return place if place is not None else self.controller.get.get_entity_object("Place", place_id=place_id)

    def _prefill_parent_field(self, parent_place):
        """Put `parent_place` into the Parent field and remember its id."""
        if parent_place is None:
            return
        self.parent_edit.setText(parent_place.place_name or "")
        self._prefill_parent = (self.parent_edit.text().strip(), parent_place.place_id)

    def _common_value(self, attr: str):
        """Value of `attr` shared by every selected place, or None when they differ."""
        values = {getattr(p, attr, None) for p in self.places}
        return values.pop() if len(values) == 1 else None

    def _populate_multi(self) -> None:
        """Prefill each field with the value all selected places share; leave the others blank."""
        self.type_edit.setText(self._common_value("place_type") or "")
        lat = self._common_value("place_latitude")
        if lat is not None:
            self.lat_edit.setText(str(lat))
        lon = self._common_value("place_longitude")
        if lon is not None:
            self.lon_edit.setText(str(lon))
        self.desc_edit.setPlainText(self._common_value("place_description") or "")
        parent_ids = {p.parent_id for p in self.places}
        if len(parent_ids) == 1:
            self._prefill_parent_field(self._place_by_id(parent_ids.pop()))

    def _mark_dirty(self, field_name: str) -> None:
        """Record that the user changed `field_name` (multi mode)."""
        self._dirty.add(field_name)

    def _set_search_status(self, text):
        """Show or hide the coordinate-search status line."""
        self.search_status.setText(text)
        self.search_status.setVisible(bool(text))
        self._grow_to_fit()

    def search_coordinates(self):
        """Look up coordinates for the name and region on a worker thread."""
        place_name = self.name_edit.text().strip()
        region = self.region_edit.text().strip()
        self.results_list.clear()
        self.results_list.hide()
        if not place_name:
            self._set_search_status("Enter a place name to search.")
            return

        query = f"{place_name}, {region}" if region else place_name
        logger.debug(f"Searching for coordinates of {query!r}")
        self._set_search_status("Searching…")
        self.search_coord_button.setEnabled(False)

        worker = _GeocodeWorker(query)
        worker.found.connect(self._on_geocode_found)
        worker.failed.connect(self._on_geocode_failed)
        worker.finished.connect(lambda w=worker: _running_geocodes.discard(w))
        _running_geocodes.add(worker)
        self._geocode_worker = worker
        worker.start()

    def _on_geocode_found(self, locations):
        """Use a single result, or list several to pick from."""
        if self.sender() is not self._geocode_worker:
            return  # result of an older search
        self.search_coord_button.setEnabled(True)
        if not locations:
            self._set_search_status("No coordinates found. Try adding a region or country.")
        elif len(locations) == 1:
            self._apply_location(locations[0])
            self._set_search_status(f"Found: {locations[0].address}")
        else:
            self._set_search_status(f"{len(locations)} matches. Click the correct one.")
            for location in locations:
                item = QListWidgetItem(f"{location.address}\n{location.latitude:.4f}, {location.longitude:.4f}")
                item.setData(Qt.UserRole, location)
                self.results_list.addItem(item)
            self.results_list.setFixedHeight(min(self.results_list.sizeHintForRow(0) * len(locations) + 6, 220))
            self.results_list.show()
            self._grow_to_fit()

    def _on_geocode_failed(self, message):
        """Show why the coordinate search failed."""
        if self.sender() is not self._geocode_worker:
            return
        self.search_coord_button.setEnabled(True)
        self._set_search_status(message)

    def done(self, result):
        """Stop a running coordinate search before the dialog closes."""
        if self._geocode_worker is not None:
            self._geocode_worker.request_cancel()
            self._geocode_worker = None
        super().done(result)

    def _use_result(self, item):
        """Use the clicked geocoder result."""
        location = item.data(Qt.UserRole)
        if location is not None:
            self._apply_location(location)
            self._set_search_status(f"Using: {location.address}")

    def _apply_location(self, location):
        """Fill the coordinate fields from a geocoder result."""
        self.lat_edit.setText(str(location.latitude))
        self.lon_edit.setText(str(location.longitude))
        self.coords_error.hide()

    def _resolve_parent(self, parent_name):
        """Return (ok, parent_id) for the Parent field's text, showing the inline error when invalid."""
        if not parent_name:
            return True, None
        # Prefer the id locked in by a completer pick, then the id the dialog
        # prefilled; a name lookup is the last resort (same-named places collide).
        parent_id = self.parent_edit.matched_id()
        if parent_id is None and self._prefill_parent is not None and parent_name == self._prefill_parent[0]:
            parent_id = self._prefill_parent[1]
        if parent_id is None:
            parent_object = self.controller.get.get_entity_object("Place", place_name=parent_name)
            parent_id = parent_object.place_id if parent_object else None
        if not parent_id:
            self._show_error(self.parent_error, f"Invalid Parent: no place is named '{parent_name}'.")
            return False, None
        moved_ids = [p.place_id for p in self.places] + ([self._new_parent_of.place_id] if self._new_parent_of is not None else [])
        if would_create_cycle(self._places_by_id, moved_ids, parent_id):
            self._show_error(self.parent_error, _CYCLE_ERROR)
            return False, None
        return True, parent_id

    def get_place_data(self):
        """Return form data as a dictionary, or None if the parent is invalid."""
        ok, parent_id = self._resolve_parent(self.parent_edit.text().strip())
        if not ok:
            return None
        return {
            "place_name": self.name_edit.text().strip(),
            "place_type": self.type_edit.text().strip(),
            "place_latitude": float(self.lat_edit.text()) if self.lat_edit.text().strip() else None,
            "place_longitude": float(self.lon_edit.text()) if self.lon_edit.text().strip() else None,
            "place_description": self.desc_edit.toPlainText().strip(),
            "parent_id": parent_id,
            "MBID": self.mbid_edit.text().strip() or None,
        }

    def get_bulk_changes(self) -> dict | None:
        """Return only the fields touched in multi mode, or None when the Parent is invalid."""
        changes: dict = {}

        if "place_type" in self._dirty:
            changes["place_type"] = self.type_edit.text().strip()
        if "place_latitude" in self._dirty:
            text = self.lat_edit.text().strip()
            changes["place_latitude"] = float(text) if text else None
        if "place_longitude" in self._dirty:
            text = self.lon_edit.text().strip()
            changes["place_longitude"] = float(text) if text else None
        if "place_description" in self._dirty:
            changes["place_description"] = self.desc_edit.toPlainText().strip()
        if "parent_id" in self._dirty:
            ok, parent_id = self._resolve_parent(self.parent_edit.text().strip())
            if not ok:
                return None
            changes["parent_id"] = parent_id

        return changes

    def _validate_coordinates(self):
        """Return True when the coordinates are valid, else show the inline error."""
        latitude, lat_ok = _parse_coordinate(self.lat_edit.text(), 90)
        longitude, lon_ok = _parse_coordinate(self.lon_edit.text(), 180)
        if not lat_ok or not lon_ok:
            self._show_error(self.coords_error, "Invalid Coordinates: latitude must be a number from -90 to 90 and longitude from -180 to 180, e.g. 36.1627 and -86.7816.")
            (self.lat_edit if not lat_ok else self.lon_edit).setFocus()
            return False
        # Multi mode can change one coordinate on purpose; one place needs both or neither.
        if not self.is_multi and (latitude is None) != (longitude is None):
            self._show_error(self.coords_error, "Enter both latitude and longitude, or leave both empty.")
            (self.lat_edit if latitude is None else self.lon_edit).setFocus()
            return False
        return True

    def validate_and_accept(self):
        """Validate the form and accept the dialog only when it is valid."""
        if not self._validate_coordinates():
            return

        if not self.is_multi and not self.name_edit.text().strip():
            self._show_error(self.name_error, "Enter a name for this place.")
            self.name_edit.setFocus()
            return

        data = self.get_bulk_changes() if self.is_multi else self.get_place_data()
        if data is None:
            self.parent_edit.setFocus()
            return

        self.accept()
