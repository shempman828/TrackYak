# ══════════════════════════════════════════════════════════════════════════════
# Tab: Places & Awards
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


def _make_table(headers, editable=True):
    """Create a standard QTableWidget with consistent styling."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    if not editable:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return t


def _set_item(table, row, col, text, user_data=None):
    item = QTableWidgetItem(str(text) if text is not None else "")
    if user_data is not None:
        item.setData(Qt.UserRole, user_data)
    table.setItem(row, col, item)


def _append_row(table, values, user_data=None):
    row = table.rowCount()
    table.insertRow(row)
    for col, val in enumerate(values):
        _set_item(table, row, col, val, user_data if col == 0 else None)
    return row


def _remove_selected_row(parent_widget, table, remove_fn):
    rows = table.selectionModel().selectedRows()
    if not rows:
        QMessageBox.information(
            parent_widget, "No Selection", "Please select a row first."
        )
        return
    remove_fn(rows[0].row())


def _parent_place_name(place):
    if place.parent_id and hasattr(place, "parent") and place.parent:
        return place.parent.place_name
    return ""


class PlacesAwardsTab(QWidget):
    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        # ── Places ──────────────────────────────────────────────────────────
        places_grp = QGroupBox("Associated Places")
        pl_layout = QVBoxLayout(places_grp)
        self.places_table = _make_table(
            ["Place Name", "Association Type", "Place Type", "Region/Country"],
            editable=False,
        )
        pl_layout.addWidget(self.places_table)
        place_help = QLabel(
            "You can type a new place name — it will be created automatically if it doesn't exist yet."
        )
        place_help.setWordWrap(True)
        place_help.setStyleSheet("color: #888; font-size: 11px;")
        pl_layout.addWidget(place_help)
        pl_add_row = QHBoxLayout()
        self.new_place_edit = QLineEdit()
        self.new_place_edit.setPlaceholderText("Place name (new or existing)...")
        self.new_place_assoc_edit = QLineEdit()
        self.new_place_assoc_edit.setPlaceholderText(
            "Relationship (e.g. Birthplace, Hometown)..."
        )
        add_place_btn = QPushButton("Link Place")
        add_place_btn.clicked.connect(self._add_place)
        rm_place_btn = QPushButton("Unlink Selected")
        rm_place_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.places_table, self._remove_place)
        )
        pl_add_row.addWidget(self.new_place_edit, 3)
        pl_add_row.addWidget(self.new_place_assoc_edit, 2)
        pl_add_row.addWidget(add_place_btn)
        pl_add_row.addWidget(rm_place_btn)
        pl_layout.addLayout(pl_add_row)
        splitter.addWidget(places_grp)

        # ── Awards ──────────────────────────────────────────────────────────
        awards_grp = QGroupBox("Awards")
        aw_layout = QVBoxLayout(awards_grp)
        self.awards_table = _make_table(
            ["Award Name", "Category", "Year"], editable=False
        )
        aw_layout.addWidget(self.awards_table)
        award_help = QLabel(
            "You can type a new award name — it will be created automatically if it doesn't exist yet."
        )
        award_help.setWordWrap(True)
        award_help.setStyleSheet("color: #888; font-size: 11px;")
        aw_layout.addWidget(award_help)
        aw_add_row = QHBoxLayout()
        self.new_award_edit = QLineEdit()
        self.new_award_edit.setPlaceholderText("Award name (new or existing)...")
        add_award_btn = QPushButton("Link Award")
        add_award_btn.clicked.connect(self._add_award)
        rm_award_btn = QPushButton("Unlink Selected")
        rm_award_btn.clicked.connect(
            lambda: _remove_selected_row(self, self.awards_table, self._remove_award)
        )
        aw_add_row.addWidget(self.new_award_edit, 3)
        aw_add_row.addWidget(add_award_btn)
        aw_add_row.addWidget(rm_award_btn)
        aw_layout.addLayout(aw_add_row)
        splitter.addWidget(awards_grp)

        layout.addWidget(splitter)

    def load(self, artist):
        self.artist = artist
        self._load_places()
        self._load_awards()

    def _load_places(self):
        self.places_table.setRowCount(0)
        assocs_loaded = False
        try:
            place_assocs = self.controller.get.get_all_entities(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )
            if place_assocs is not None:
                for assoc in place_assocs:
                    if assoc.place is None:
                        continue
                    _append_row(
                        self.places_table,
                        [
                            assoc.place.place_name,
                            assoc.association_type or "",
                            assoc.place.place_type or "",
                            _parent_place_name(assoc.place),
                        ],
                        user_data=assoc.association_id,
                    )
                assocs_loaded = True
        except Exception as e:
            logger.debug(f"Could not load via PlaceAssociation entities: {e}")
        if not assocs_loaded:
            for place in getattr(self.artist, "places", []):
                _append_row(
                    self.places_table,
                    [
                        place.place_name,
                        "",
                        place.place_type or "",
                        _parent_place_name(place),
                    ],
                    user_data=place.place_id,
                )

    def _load_awards(self):
        self.awards_table.setRowCount(0)
        assocs_loaded = False
        try:
            award_assocs = self.controller.get.get_all_entities(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )
            if award_assocs is not None:
                for assoc in award_assocs:
                    if assoc.award is None:
                        continue
                    _append_row(
                        self.awards_table,
                        [
                            assoc.award.award_name,
                            assoc.award.award_category or "",
                            assoc.award.award_year or "",
                        ],
                        user_data=assoc.association_id,
                    )
                assocs_loaded = True
        except Exception as e:
            logger.debug(f"Could not load via AwardAssociation entities: {e}")
        if not assocs_loaded:
            for award in getattr(self.artist, "awards", []):
                _append_row(
                    self.awards_table,
                    [
                        award.award_name,
                        award.award_category or "",
                        award.award_year or "",
                    ],
                    user_data=award.award_id,
                )

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _add_place(self):
        name = self.new_place_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a place name.")
            return
        association_type = self.new_place_assoc_edit.text().strip()
        if not association_type:
            QMessageBox.warning(
                self,
                "Validation",
                "Please enter the relationship type (e.g. Birthplace, Hometown).",
            )
            return
        try:
            place = self.controller.get.get_entity_object("Place", place_name=name)
            if isinstance(place, list):
                place = place[0] if place else None
            if place is None:
                place = self.controller.add.add_entity("Place", place_name=name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create place:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place.place_id,
                association_type=association_type,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not link place:\n{e}")
            return
        self._reload_and_refresh()
        self.new_place_edit.clear()
        self.new_place_assoc_edit.clear()

    def _remove_place(self, row):
        assoc_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        try:
            self.controller.delete.delete_entity("PlaceAssociation", assoc_id)
        except Exception:
            try:
                self.controller.delete.delete_entity(
                    "PlaceAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                    place_id=assoc_id,
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not unlink place:\n{e}")
                return
        self._reload_and_refresh()

    def _add_award(self):
        name = self.new_award_edit.text().strip()
        if not name:
            return
        try:
            awards = self.controller.get.get_entity_object("Award", award_name=name)
            if isinstance(awards, list):
                award = awards[0] if awards else None
            else:
                award = awards
            if award is None:
                award = self.controller.add.add_entity("Award", award_name=name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create award:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                award_id=award.award_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not link award:\n{e}")
            return
        self._reload_and_refresh()
        self.new_award_edit.clear()

    def _remove_award(self, row):
        assoc_id = self.awards_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        try:
            self.controller.delete.delete_entity("AwardAssociation", assoc_id)
        except Exception:
            try:
                self.controller.delete.delete_entity(
                    "AwardAssociation",
                    entity_id=self.artist.artist_id,
                    entity_type="Artist",
                    award_id=assoc_id,
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not unlink award:\n{e}")
                return
        self._reload_and_refresh()
