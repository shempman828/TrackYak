# ---------------------------------------------------------------------------
# PlacesTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab


def _fetch_all_places(controller):
    """Fetch all places for completer indexing."""
    try:
        return controller.get.get_all_entities("Place") or []
    except Exception as e:
        logger.warning(f"Could not fetch places for completer: {e}")
        return []


def _find_or_create_place(controller, name, known_places):
    """
    Look up a place by name (case-insensitive) among known places; create one
    only if none is found. Matching against the already-loaded list (rather
    than a fresh DB query) guarantees an existing place always wins over
    creating a same-named duplicate, regardless of case.
    """
    lowered = name.strip().lower()
    for place in known_places:
        if (place.place_name or "").strip().lower() == lowered:
            return place
    return controller.add.add_entity("Place", place_name=name)


class _PlaceNameEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over known places. When the user picks a
    completion, we remember the matched place_id directly so add-time skips
    the name-lookup roundtrip entirely (and can't collide with a same-named
    place). Any manual edit after a match clears the lock — typing a new
    name always means "maybe create new."
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Search places…")
        self._display_to_id = {}
        self._matched_id = None
        self.textEdited.connect(self._on_manual_edit)

    def set_index(self, display_to_id: dict):
        """Rebuild the completer's backing model."""
        self._display_to_id = dict(display_to_id)
        model = QStringListModel(sorted(self._display_to_id.keys()), self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._on_completion_picked)
        self.setCompleter(completer)

    def _on_completion_picked(self, text):
        self._matched_id = self._display_to_id.get(text)

    def _on_manual_edit(self, _text):
        self._matched_id = None

    def matched_place_id(self):
        return self._matched_id

    def reset(self):
        self.clear()
        self._matched_id = None


class PlacesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = _PlaceNameEdit()
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("Type (Recorded, Composed, etc.)")
        self._type_edit.returnPressed.connect(self._add)
        search_row.addWidget(self._type_edit)

        self._add_btn = QPushButton("Add Place")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Place", "Type", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _refresh_completer_index(self):
        self._known_places = _fetch_all_places(self.controller)
        index = {p.place_name: p.place_id for p in self._known_places if p.place_name}
        self._search.set_index(index)

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._table.setRowCount(0)
        if self.is_multi:
            rows = self._common_places()
        else:
            assocs = self.controller.get.get_entity_links(
                "PlaceAssociation", entity_id=self.track.track_id, entity_type="Track"
            )
            rows = []
            for a in assocs:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=a.place_id
                )
                if place:
                    rows.append(
                        (place.place_id, place.place_name, a.association_type or "")
                    )
        for place_id, place_name, assoc_type in rows:
            self._add_row(place_id, place_name, assoc_type)

    def _common_places(self):
        all_sets = []
        for t in self.tracks:
            s = set()
            assocs = self.controller.get.get_entity_links(
                "PlaceAssociation", entity_id=t.track_id, entity_type="Track"
            )
            for a in assocs:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=a.place_id
                )
                if place:
                    s.add((place.place_id, place.place_name, a.association_type or ""))
            all_sets.append(s)
        common = all_sets[0]
        for s in all_sets[1:]:
            common &= s
        return list(common)

    def _add_row(self, place_id, place_name, assoc_type):
        row = self._table.rowCount()
        self._table.insertRow(row)
        pi = QTableWidgetItem(place_name)
        pi.setData(Qt.UserRole, place_id)
        self._table.setItem(row, 0, pi)
        self._table.setItem(row, 1, QTableWidgetItem(assoc_type))
        btn = QPushButton("Remove")
        btn.clicked.connect(lambda _c, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 2, btn)

    def _add(self):
        place_name = self._search.text().strip()
        assoc_type = self._type_edit.text().strip() or None
        if not place_name:
            return

        matched_id = self._search.matched_place_id()
        try:
            if matched_id is not None:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=matched_id
                )
            else:
                place = _find_or_create_place(
                    self.controller, place_name, self._known_places
                )
        except Exception as e:
            logger.error(f"Failed to find/create place: {e}")
            return
        if not place:
            return

        rows = [
            {
                "entity_id": track.track_id,
                "entity_type": "Track",
                "place_id": place.place_id,
                "association_type": assoc_type,
            }
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities("PlaceAssociation", rows)
        except Exception as e:
            logger.error(f"Failed to add place to tracks: {e}")
        self._search.reset()
        self._type_edit.clear()
        self._refresh_completer_index()
        self.load(self.tracks)

    def _remove_row(self, row: int):
        place_item = self._table.item(row, 0)
        if not place_item:
            return
        place_id = place_item.data(Qt.UserRole)
        track_ids = [track.track_id for track in self.tracks]
        try:
            self.controller.delete.delete_entity(
                "PlaceAssociation",
                entity_id=track_ids,
                entity_type="Track",
                place_id=place_id,
            )
        except Exception as e:
            logger.error(f"Failed to remove place from tracks: {e}")
        self.load(self.tracks)
