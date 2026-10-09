"""PlacesTab: link places (with an association type) to the edited track(s)."""

from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from src.common.widgets.entity_completer_context import place_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget, find_or_create_by_name, get_cached_entities, register_cached_entity
from src.foundation.logger_config import logger
from src.place.place_association_types import fetch_association_types, find_or_create_association_type
from src.track.edit.track_edit_basetab import _BaseTab


def _find_or_create_place(controller, name, known_places):
    """Return the place named `name` (case-insensitive) from `known_places`, else a new place."""
    return find_or_create_by_name(controller, "Place", "place_name", name, known_places)


def _read_only_item(text: str) -> QTableWidgetItem:
    """Return a table item the user cannot edit."""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class PlacesTab(_BaseTab):
    """Link and unlink places; each link has an optional association type."""

    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_type_completer()

    def _build_ui(self):
        """Build the place search row and the linked-places table."""
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = build_entity_search_widget(self.controller, "Place", "place_name", "place_id", "Search places…", context_builder=place_context_map)
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("Type (Recording Location, Origin, etc.)")
        self._type_edit.returnPressed.connect(self._add)
        search_row.addWidget(self._type_edit)

        self._add_btn = QPushButton("Add Place")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Place", "Type", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

    def _on_search_text_changed(self, text: str):
        """Enable "Add Place" when the search has text."""
        self._add_btn.setEnabled(bool(text.strip()))

    def _known_places(self) -> list:
        """Return the places to check for a case-insensitive duplicate name."""
        # The full cached table when small enough, else the widget's last query.
        cached = get_cached_entities(self.controller, "Place")
        if cached is not None:
            return cached
        return self._search.known_matches()

    def _refresh_type_completer(self):
        """Load the association types and attach them as the type field's completer."""
        # A small, near-static lookup table, so a full preload stays cheap.
        self._known_types = fetch_association_types(self.controller)
        type_model = QStringListModel([t.type_name for t in self._known_types], self._type_edit)
        type_completer = QCompleter(type_model, self._type_edit)
        type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        type_completer.setFilterMode(Qt.MatchContains)
        self._type_edit.setCompleter(type_completer)

    def _track_ids(self) -> list[int]:
        """Return the ids of the edited tracks."""
        return [t.track_id for t in self.tracks]

    def _associations(self) -> list:
        """Return every place association of the edited tracks, in one query."""
        return self.controller.get.get_entity_links("PlaceAssociation", entity_type="Track", entity_id__in=self._track_ids()) or []

    def load(self, tracks: list) -> None:
        """Show the (place, type) links shared by every edited track."""
        self.tracks = tracks
        self._table.setRowCount(0)

        keys_by_track: dict[int, set[tuple]] = {tid: set() for tid in self._track_ids()}
        for assoc in self._associations():
            keys_by_track.setdefault(assoc.entity_id, set()).add((assoc.place_id, assoc.association_type_id))
        common = set.intersection(*keys_by_track.values()) if keys_by_track else set()
        if not common:
            return

        places = self.controller.get.get_all_entities("Place", place_id__in=sorted({pid for pid, _ in common})) or []
        place_names = {p.place_id: p.place_name for p in places}
        type_names = {t.association_type_id: t.type_name for t in self._known_types}
        rows = [(pid, tid, place_names[pid], type_names.get(tid, "")) for pid, tid in common if pid in place_names]
        for place_id, type_id, place_name, type_name in sorted(rows, key=lambda r: (r[2].lower(), r[3].lower())):
            self._add_row(place_id, type_id, place_name, type_name)

    def _add_row(self, place_id, type_id, place_name, type_name):
        """Append one place row with a Remove button."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        place_item = _read_only_item(place_name)
        place_item.setData(Qt.UserRole, place_id)
        self._table.setItem(row, 0, place_item)
        type_item = _read_only_item(type_name)
        type_item.setData(Qt.UserRole, type_id)
        self._table.setItem(row, 1, type_item)
        btn = QPushButton("Remove")
        btn.setToolTip(f"Unlink '{place_name}' ({type_name or 'no type'}) from the edited track(s)")
        btn.clicked.connect(lambda _c, pid=place_id, tid=type_id: self._remove(pid, tid))
        self._table.setCellWidget(row, 2, btn)

    def _add(self):
        """Link the searched place(s), with the typed association type, to every edited track."""
        place_names = self._search.split_names()
        assoc_type = self._type_edit.text().strip() or None
        if not place_names:
            return

        # matched_id only names a single typed place; several names are each resolved by name.
        single_matched_id = self._search.matched_id() if len(place_names) == 1 else None
        places = []
        for place_name in place_names:
            if single_matched_id is not None:
                place = self.controller.get.get_entity_object("Place", place_id=single_matched_id)
            else:
                place = _find_or_create_place(self.controller, place_name, self._known_places())
            if place:
                places.append(place)
        if not places:
            QMessageBox.warning(self, "Error", "Could not find or create the place.")
            return

        assoc_type_obj = find_or_create_association_type(self.controller, assoc_type, self._known_types)
        type_id = assoc_type_obj.association_type_id if assoc_type_obj else None
        # PlaceAssociation has a surrogate key, so add_entities cannot skip duplicates itself.
        existing = {(a.entity_id, a.place_id, a.association_type_id) for a in self._associations()}
        rows = [
            {"entity_id": tid, "entity_type": "Track", "place_id": place.place_id, "association_type_id": type_id}
            for place in places
            for tid in self._track_ids()
            if (tid, place.place_id, type_id) not in existing
        ]
        if rows and not self.controller.add.add_entities("PlaceAssociation", rows):
            logger.error(f"Failed to add place(s) {[p.place_id for p in places]} to tracks {self._track_ids()}")
            QMessageBox.warning(self, "Error", "Could not add the place. See the log for details.")
            return

        if single_matched_id is None:
            for place in places:
                # Deferred: add_to_index() rebuilds the completer, which is unsafe mid key-dispatch (Enter).
                place_name, place_id = place.place_name, place.place_id
                QTimer.singleShot(0, lambda n=place_name, i=place_id: self._search.add_to_index(n, i))
                register_cached_entity("Place", place)

        self._search.reset()
        self._type_edit.clear()
        self._refresh_type_completer()
        self.load(self.tracks)

    def _remove_row(self, row: int):
        """Unlink the (place, type) pair shown in table row `row`."""
        place_item, type_item = self._table.item(row, 0), self._table.item(row, 1)
        if place_item is None or type_item is None:
            return
        self._remove(place_item.data(Qt.UserRole), type_item.data(Qt.UserRole))

    def _remove(self, place_id: int, type_id: int | None):
        """Unlink one (place, type) pair from every edited track."""
        assoc_ids = [a.association_id for a in self._associations() if a.place_id == place_id and a.association_type_id == type_id]
        if assoc_ids and not self.controller.delete.delete_entity("PlaceAssociation", entity_ids=assoc_ids):
            logger.error(f"Failed to remove place {place_id} (type {type_id}) from tracks {self._track_ids()}")
            QMessageBox.warning(self, "Error", "Could not remove the place. See the log for details.")
            return
        self.load(self.tracks)
