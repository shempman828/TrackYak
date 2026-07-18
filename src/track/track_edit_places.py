# ---------------------------------------------------------------------------
# PlacesTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.common.entity_completer_edit import find_or_create_by_name
from src.core.logger_config import logger
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.track.track_edit_basetab import _BaseTab

# Search results are capped and fetched on demand (min 2 chars) instead of
# preloading every Place row into a completer index on tab open -- see
# track_edit_samples.py for the tab that made this expensive with the
# library's Track table; Place is small today but would hit the same wall
# as it grows.
_MAX_SEARCH_RESULTS = 50


def _search_places(controller, text: str):
    """Bounded, on-demand lookup of places whose name contains `text`."""
    try:
        matches = controller.get.get_all_entities("Place", place_name__contains=text) or []
        return matches[:_MAX_SEARCH_RESULTS]
    except Exception as e:
        logger.warning(f"Could not search places: {e}")
        return []


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
        self._known_places: list = []
        self._build_ui()
        self._refresh_type_completer()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search places… (min 2 chars)")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._add)
        search_row.addWidget(self._search)

        self._place_combo = QComboBox()
        self._place_combo.setVisible(False)
        self._place_combo.currentIndexChanged.connect(self._on_place_selected)
        search_row.addWidget(self._place_combo)

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
        text = text.strip()
        self._add_btn.setEnabled(bool(text))
        self._place_combo.blockSignals(True)
        self._place_combo.clear()
        if len(text) >= 2:
            self._known_places = _search_places(self.controller, text)
            for p in self._known_places:
                if p.place_name:
                    self._place_combo.addItem(p.place_name, p.place_id)
            self._place_combo.setVisible(self._place_combo.count() > 0)
        else:
            self._place_combo.setVisible(False)
        self._place_combo.blockSignals(False)

    def _on_place_selected(self, index: int):
        if index >= 0:
            self._search.blockSignals(True)
            self._search.setText(self._place_combo.currentText())
            self._search.blockSignals(False)

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

        combo_data = (
            self._place_combo.currentData() if self._place_combo.isVisible() else None
        )
        try:
            if combo_data is not None:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=combo_data
                )
            else:
                # Bounded candidate set for the duplicate check, fetched
                # fresh rather than kept as a stale full-table cache.
                known_places = _search_places(self.controller, place_name)
                place = _find_or_create_place(self.controller, place_name, known_places)
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
        self._search.clear()
        self._place_combo.blockSignals(True)
        self._place_combo.clear()
        self._place_combo.setVisible(False)
        self._place_combo.blockSignals(False)
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
