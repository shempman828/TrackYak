# ---------------------------------------------------------------------------
# PlacesTab
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class PlacesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search places… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("Type (Recorded, Composed, etc.)")
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

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Place", place_name=text)
            self._combo.addItem(f"Create new: '{text}'", "new")
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for p in items:
                    self._combo.addItem(p.place_name, p.place_id)
            self._combo.setVisible(self._combo.count() > 1)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2)

    def _on_selected(self, index: int):
        if index > 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        place_name = self._search.text().strip()
        assoc_type = self._type_edit.text().strip() or None
        if not place_name:
            return
        combo_data = self._combo.currentData() if self._combo.isVisible() else None
        if combo_data and combo_data != "new":
            place = self.controller.get.get_entity_object("Place", place_id=combo_data)
        else:
            existing = self.controller.get.get_entity_object(
                "Place", place_name=place_name
            )
            if existing:
                place = existing if not isinstance(existing, list) else existing[0]
            else:
                place = self.controller.add.add_entity("Place", place_name=place_name)
        if not place:
            return
        for track in self.tracks:
            try:
                self.controller.add.add_entity(
                    "PlaceAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    place_id=place.place_id,
                    association_type=assoc_type,
                )
            except Exception as e:
                logger.error(f"Failed to add place to track {track.track_id}: {e}")
        self._search.clear()
        self._type_edit.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_row(self, row: int):
        place_item = self._table.item(row, 0)
        if not place_item:
            return
        place_id = place_item.data(Qt.UserRole)
        for track in self.tracks:
            try:
                self.controller.delete.delete_entity(
                    "PlaceAssociation",
                    entity_id=track.track_id,
                    entity_type="Track",
                    place_id=place_id,
                )
            except Exception as e:
                logger.error(f"Failed to remove place from track {track.track_id}: {e}")
        self.load(self.tracks)
