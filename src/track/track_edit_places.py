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

from src.common.entity_completer_edit import (
    build_entity_search_widget,
    find_or_create_by_name,
    get_cached_entities,
    register_cached_entity,
)
from src.core.logger_config import logger
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.track.track_edit_basetab import _BaseTab


def _find_or_create_place(controller, name, known_places):
    """
    Look up a place by name (case-insensitive) among known places; create one
    only if none is found. Matching against the already-loaded list (rather
    than a fresh DB query) guarantees an existing place always wins over
    creating a same-named duplicate, regardless of case.
    """
    return find_or_create_by_name(controller, "Place", "place_name", name, known_places)


class PlacesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_type_completer()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = build_entity_search_widget(
            self.controller, "Place", "place_name", "place_id", "Search places…"
        )
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
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        layout.addWidget(self._table)

    def _on_search_text_changed(self, text: str):
        self._add_btn.setEnabled(bool(text.strip()))

    def _known_places(self) -> list:
        """Candidate set for _find_or_create_place's case-insensitive
        duplicate check: the full cached table when small enough to
        preload, or the bounded search widget's last on-demand query."""
        cached = get_cached_entities(self.controller, "Place")
        if cached is not None:
            return cached
        return self._search.known_matches()

    def _refresh_type_completer(self):
        # Association types (Recording Location, Origin, ...) are a small,
        # near-static lookup table -- not per-track/library-scaling like
        # Place, so a full preload here stays cheap.
        self._known_types = fetch_association_types(self.controller)
        type_model = QStringListModel(
            [t.type_name for t in self._known_types], self._type_edit
        )
        type_completer = QCompleter(type_model, self._type_edit)
        type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        type_completer.setFilterMode(Qt.MatchContains)
        self._type_edit.setCompleter(type_completer)

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
                    assoc_type_name = (
                        a.association_type.type_name if a.association_type else ""
                    )
                    rows.append((place.place_id, place.place_name, assoc_type_name))
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
                    assoc_type_name = (
                        a.association_type.type_name if a.association_type else ""
                    )
                    s.add((place.place_id, place.place_name, assoc_type_name))
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

        matched_id = self._search.matched_id()
        try:
            if matched_id is not None:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=matched_id
                )
            else:
                place = _find_or_create_place(
                    self.controller, place_name, self._known_places()
                )
        except Exception as e:
            logger.error(f"Failed to find/create place: {e}")
            return
        if not place:
            return

        assoc_type_obj = find_or_create_association_type(
            self.controller, assoc_type, self._known_types
        )
        rows = [
            {
                "entity_id": track.track_id,
                "entity_type": "Track",
                "place_id": place.place_id,
                "association_type_id": assoc_type_obj.association_type_id
                if assoc_type_obj
                else None,
            }
            for track in self.tracks
        ]
        try:
            self.controller.add.add_entities("PlaceAssociation", rows)
        except Exception as e:
            logger.error(f"Failed to add place to tracks: {e}")

        if matched_id is None:
            self._search.add_to_index(place.place_name, place.place_id)
            register_cached_entity("Place", place)

        self._search.reset()
        self._type_edit.clear()
        self._refresh_type_completer()
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
