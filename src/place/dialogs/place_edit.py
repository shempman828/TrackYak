from geopy import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.entity_completer_context import place_context_map
from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.foundation.logger_config import logger

# Geocoder results to offer when a name is ambiguous.
_GEOCODE_LIMIT = 5


class PlaceEditDialog(QDialog):
    """Form dialog for creating/editing places -- or bulk-editing several at
    once when `place` is a list (see `is_multi`)."""

    def __init__(self, controller, parent=None, place=None):
        super().__init__(parent)
        self.controller = controller
        self.places = place if isinstance(place, list) else ([place] if place else [])
        self.is_multi = len(self.places) > 1
        # Convenience property, only meaningful when is_multi is False.
        self.place = self.places[0] if self.places else None
        # Tracks which fields the user has actually touched in multi mode --
        # only these are written to every selected place on save. Unused
        # (and irrelevant) in single-place mode, which always writes every
        # field like it always has.
        self._dirty: set = set()
        self.geolocator = Nominatim(user_agent="place_manager")
        self.init_ui()

    def init_ui(self):
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

        # Form fields. Name, MBID, and the coordinate-search tools identify
        # one specific real-world place, so they're left out of multi-edit
        # mode entirely (see docs/specs/place-list-multi-edit.md).
        if not self.is_multi:
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("e.g. Nashville")
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("e.g. City, Studio, Country")
        self.lat_edit = QLineEdit()
        self.lat_edit.setPlaceholderText("Latitude")
        self.lon_edit = QLineEdit()
        self.lon_edit.setPlaceholderText("Longitude")
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

        # Setup autocompletion for parent_edit. Keying on place_name locks
        # in the selected place_id when a completion is picked, so save-time
        # doesn't have to re-resolve the parent by a name that could collide
        # with another place or no longer exist.
        places = self.controller.get.get_all_entities("Place")
        parent_index = {p.place_name: p.place_id for p in places if p.place_name}
        self.parent_edit.set_index(parent_index, place_context_map(places))

        # Suggest existing place types as the user types, without
        # restricting entry to only those types.
        known_types = sorted({p.place_type.strip().title() for p in places if p.place_type and p.place_type.strip()})
        type_model = QStringListModel(known_types, self.type_edit)
        type_completer = QCompleter(type_model, self.type_edit)
        type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        type_completer.setFilterMode(Qt.MatchContains)
        self.type_edit.setCompleter(type_completer)
        # EntityCompleterEdit claims Enter/Return for its own use (see its
        # docstring) instead of letting it reach the dialog's default
        # button -- reconnect it here so Enter still submits this form like
        # every other field does via QDialogButtonBox.
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
            # Mark a field dirty only on actual user interaction --
            # textEdited (unlike textChanged) never fires from the
            # programmatic setText() calls _populate_multi() just made, so
            # prefilled-but-untouched fields correctly stay out of
            # self._dirty. picked covers a parent chosen from the
            # completer popup, which also doesn't fire textEdited.
            # QPlainTextEdit has no textEdited, so its textChanged is
            # connected only after the prefill above has run.
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
            parent = self.controller.get.get_entity_object("Place", place_id=self.place.parent_id)
            self.parent_edit.setText(parent.place_name if parent else "")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        return form

    @staticmethod
    def _error_label():
        label = QLabel()
        label.setProperty("textRole", "error")
        label.setWordWrap(True)
        label.hide()
        return label

    def _show_error(self, label, text):
        label.setText(text)
        label.show()
        self._grow_to_fit()

    def _grow_to_fit(self):
        """Grow the dialog when an inline message or result list appears,
        instead of letting the form rows squeeze over each other."""

        def grow():
            needed = self.layout().totalHeightForWidth(self.width()) if self.layout().hasHeightForWidth() else self.sizeHint().height()
            if needed > self.height():
                self.resize(self.width(), needed)

        QTimer.singleShot(0, grow)

    def _common_value(self, attr: str):
        """Return the value of `attr` shared by every place in self.places,
        or None if the selection disagrees (or every place is None)."""
        values = {getattr(p, attr, None) for p in self.places}
        return values.pop() if len(values) == 1 else None

    def _common_parent_name(self) -> str:
        """Return the shared parent's name across self.places, or "" if
        they disagree or share no parent."""
        parent_ids = {p.parent_id for p in self.places}
        if len(parent_ids) != 1:
            return ""
        parent_id = parent_ids.pop()
        if parent_id is None:
            return ""
        parent = self.controller.get.get_entity_object("Place", place_id=parent_id)
        return parent.place_name if parent else ""

    def _populate_multi(self) -> None:
        """Prefill each field with its value shared across every selected
        place, leaving fields the selection disagrees on blank."""
        self.type_edit.setText(self._common_value("place_type") or "")
        lat = self._common_value("place_latitude")
        if lat is not None:
            self.lat_edit.setText(str(lat))
        lon = self._common_value("place_longitude")
        if lon is not None:
            self.lon_edit.setText(str(lon))
        self.desc_edit.setPlainText(self._common_value("place_description") or "")
        self.parent_edit.setText(self._common_parent_name())

    def _mark_dirty(self, field_name: str) -> None:
        self._dirty.add(field_name)

    def _set_search_status(self, text):
        self.search_status.setText(text)
        self.search_status.setVisible(bool(text))
        self._grow_to_fit()

    def search_coordinates(self):
        """Look up coordinates from the name and region; ambiguous names list
        their candidates inline to pick from."""
        place_name = self.name_edit.text().strip()
        region = self.region_edit.text().strip()
        self.results_list.clear()
        self.results_list.hide()
        if not place_name:
            self._set_search_status("Enter a place name to search.")
            return

        logger.debug(f"Searching for place {place_name} in region {region}")
        self._set_search_status("Searching…")
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            query = f"{place_name}, {region}" if region else place_name
            locations = self.geolocator.geocode(query, exactly_one=False, limit=_GEOCODE_LIMIT, language="en")
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            self._set_search_status(f"The coordinate search failed: {e!s}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()

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

    def _use_result(self, item):
        location = item.data(Qt.UserRole)
        if location is not None:
            self._apply_location(location)
            self._set_search_status(f"Using: {location.address}")

    def _apply_location(self, location):
        self.lat_edit.setText(str(location.latitude))
        self.lon_edit.setText(str(location.longitude))
        self.coords_error.hide()

    def _resolve_parent(self, parent_name):
        """Return (ok, parent_id) for the Parent field's text, showing the
        inline error when the name does not match a place."""
        if not parent_name:
            return True, None
        # Prefer the id locked in when the user picked a completion --
        # avoids re-resolving by name (ambiguous with same-named places)
        # or missing a place created after this dialog's index was
        # built. Falls back to a name lookup for the edit-mode prefill,
        # which sets the text directly and never locks an id.
        parent_id = self.parent_edit.matched_id()
        if parent_id is None:
            parent_object = self.controller.get.get_entity_object("Place", place_name=parent_name)
            parent_id = parent_object.place_id if parent_object else None
        if not parent_id:
            self._show_error(self.parent_error, f"Invalid Parent: no place is named '{parent_name}'.")
            return False, None
        return True, parent_id

    def get_place_data(self):
        """Return form data as dictionary, or None if the parent is invalid."""
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
        """Return only the fields the user actually touched in multi-edit
        mode, ready to hand to `update_entities`. Mirrors get_place_data()'s
        Parent Place resolution but limited to self._dirty, so an untouched
        field -- even one left blank because the selection disagreed on it
        -- is never included. Returns None (after showing the inline error)
        if a touched field fails validation, same contract as get_place_data()."""
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

    def validate_and_accept(self):
        """Validate form data and accept the dialog if valid."""
        # Validate latitude and longitude (if provided) before get_place_data()
        # converts them with an unguarded float(), which would otherwise raise
        # an uncaught ValueError on non-numeric text (e.g. a stale "None").
        for edit in (self.lat_edit, self.lon_edit):
            text = edit.text().strip()
            if not text:
                continue
            try:
                float(text)
            except ValueError:
                self._show_error(self.coords_error, "Invalid Coordinates: latitude and longitude must be numbers, e.g. 36.1627 and -86.7816.")
                edit.setFocus()
                return

        if not self.is_multi and not self.name_edit.text().strip():
            self._show_error(self.name_error, "Enter a name for this place.")
            self.name_edit.setFocus()
            return

        data = self.get_bulk_changes() if self.is_multi else self.get_place_data()
        if data is None:
            self.parent_edit.setFocus()
            return  # Validation failed, do not close the dialog

        self.accept()
