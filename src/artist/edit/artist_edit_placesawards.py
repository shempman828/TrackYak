# ══════════════════════════════════════════════════════════════════════════════
# Tab: Places & Awards
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCompleter,
    QDialog,
    QDialogButtonBox,
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
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)


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


def _with_selected_row(parent_widget, table, fn):
    """Call fn(row) for the table's selected row, or hint that one is needed."""
    rows = table.selectionModel().selectedRows()
    if not rows:
        show_status_message(parent_widget, "Please select a row first.")
        return
    fn(rows[0].row())


class _EditAssociationTypeDialog(QDialog):
    """Small modal for editing an existing place association's relationship type."""

    def __init__(self, current_type, known_types, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Relationship Type")

        layout = QVBoxLayout(self)
        self.type_edit = QLineEdit(current_type or "")
        self.type_edit.setPlaceholderText("Relationship (e.g. Birthplace, Hometown)")
        completer = QCompleter(known_types, self.type_edit)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.type_edit.setCompleter(completer)
        layout.addWidget(self.type_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.type_edit.setFocus()
        self.type_edit.selectAll()

    def value(self) -> str:
        return self.type_edit.text().strip()


def _parent_place_name(place):
    if place.parent_id and hasattr(place, "parent") and place.parent:
        return place.parent.place_name
    return ""


def _build_place_completer_model(controller):
    """Build a QStandardItemModel over existing places, disambiguated by type/region."""
    model = QStandardItemModel()
    try:
        places = controller.get.get_all_entities("Place") or []
    except SQLAlchemyError as e:
        logger.debug(f"Could not load places for autocomplete: {e}")
        places = []

    for place in places:
        region = _parent_place_name(place)
        ptype = place.place_type or ""
        detail_parts = [p for p in (ptype, region) if p]
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        display = f"{place.place_name}{detail}"
        item = QStandardItem(display)
        item.setData(place.place_id, Qt.UserRole)
        item.setData(place.place_name, Qt.UserRole + 1)
        model.appendRow(item)
    return model


def _build_place_completer(controller):
    """Build a QCompleter over existing places, disambiguated by type/region."""
    completer = QCompleter()
    completer.setModel(_build_place_completer_model(controller))
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    return completer


class PlacesAwardsTab(QWidget):
    """Link/unlink/edit an artist's associated places and awards."""

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
            ["Place Name", "Association Type", "Place Type", "Region/Country"], editable=False
        )
        self.places_table.cellDoubleClicked.connect(lambda row, _col: self._edit_place(row))
        pl_layout.addWidget(self.places_table)
        place_help = QLabel(
            "You can type a new place name — it will be created automatically if it "
            "doesn't exist yet."
        )
        place_help.setWordWrap(True)
        place_help.setProperty("textRole", "muted")
        pl_layout.addWidget(place_help)

        # ---- Place input row ----
        pl_add_row = QHBoxLayout()

        # Visible line edit for place name - this is the one we keep
        self.new_place_edit = QLineEdit()
        self.new_place_edit.setPlaceholderText("Place name (new or existing)...")
        self._selected_place_id = None

        # Build and attach completer to this visible edit
        self._place_completer = _build_place_completer(self.controller)
        self._place_completer.activated[str].connect(self._on_place_completion_selected)
        self.new_place_edit.setCompleter(self._place_completer)
        self.new_place_edit.textEdited.connect(self._on_place_text_edited)

        self.new_place_assoc_edit = QLineEdit()
        self.new_place_assoc_edit.setPlaceholderText("Relationship (e.g. Birthplace, Hometown)")
        self._assoc_type_completer = QCompleter(
            [t.type_name for t in fetch_association_types(self.controller)]
        )
        self._assoc_type_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._assoc_type_completer.setFilterMode(Qt.MatchContains)
        self.new_place_assoc_edit.setCompleter(self._assoc_type_completer)

        add_place_btn = QPushButton("Link Place")
        add_place_btn.clicked.connect(self._add_place)
        edit_place_btn = QPushButton("Edit Selected")
        edit_place_btn.setToolTip("Edit the relationship type of the selected place link")
        edit_place_btn.clicked.connect(
            lambda: _with_selected_row(self, self.places_table, self._edit_place)
        )
        rm_place_btn = QPushButton("Unlink Selected")
        rm_place_btn.clicked.connect(
            lambda: _with_selected_row(self, self.places_table, self._remove_place)
        )

        pl_add_row.addWidget(self.new_place_edit, 3)
        pl_add_row.addWidget(self.new_place_assoc_edit, 2)
        pl_add_row.addWidget(add_place_btn)
        pl_add_row.addWidget(edit_place_btn)
        pl_add_row.addWidget(rm_place_btn)
        pl_layout.addLayout(pl_add_row)
        splitter.addWidget(places_grp)

        # ── Awards ──────────────────────────────────────────────────────────
        awards_grp = QGroupBox("Awards")
        aw_layout = QVBoxLayout(awards_grp)
        self.awards_table = _make_table(["Award Name", "Category", "Year"], editable=False)
        aw_layout.addWidget(self.awards_table)
        award_help = QLabel(
            "You can type a new award name — it will be created automatically if it "
            "doesn't exist yet."
        )
        award_help.setWordWrap(True)
        award_help.setProperty("textRole", "muted")
        aw_layout.addWidget(award_help)

        aw_add_row = QHBoxLayout()
        self.new_award_edit = QLineEdit()
        self.new_award_edit.setPlaceholderText("Award name (new or existing)...")
        add_award_btn = QPushButton("Link Award")
        add_award_btn.clicked.connect(self._add_award)
        rm_award_btn = QPushButton("Unlink Selected")
        rm_award_btn.clicked.connect(
            lambda: _with_selected_row(self, self.awards_table, self._remove_award)
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
        # get_all_entities() already catches its own SQLAlchemyError and
        # returns [] on failure rather than raising, so there is exactly one
        # source of truth here -- no dead-except fallback to artist.places.
        self.places_table.setRowCount(0)
        place_assocs = self.controller.get.get_all_entities(
            "PlaceAssociation", entity_id=self.artist.artist_id, entity_type="Artist"
        )
        for assoc in place_assocs or []:
            if assoc.place is None:
                continue
            _append_row(
                self.places_table,
                [
                    assoc.place.place_name,
                    assoc.association_type.type_name if assoc.association_type else "",
                    assoc.place.place_type or "",
                    _parent_place_name(assoc.place),
                ],
                user_data=assoc.association_id,
            )

    def _load_awards(self):
        self.awards_table.setRowCount(0)
        award_assocs = self.controller.get.get_all_entities(
            "AwardAssociation", entity_id=self.artist.artist_id, entity_type="Artist"
        )
        for assoc in award_assocs or []:
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

    def _add_place(self):
        name = self.new_place_edit.text().strip()
        if not name:
            show_status_message(self, "Please enter a place name.")
            return
        association_type = self.new_place_assoc_edit.text().strip()
        if not association_type:
            show_status_message(
                self, "Please enter the relationship type (e.g. Birthplace, Hometown)."
            )
            return

        try:
            if self._selected_place_id is not None:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=self._selected_place_id
                )
                if place is None:
                    # The place picked from the completer no longer exists
                    # (e.g. deleted elsewhere since); fall back to a
                    # name-based lookup/create instead of crashing below.
                    place = self.controller.get.get_entity_object("Place", place_name=name)
                    if place is None:
                        place = self.controller.add.add_entity("Place", place_name=name)
            else:
                place = self.controller.get.get_entity_object("Place", place_name=name)
                if place is None:
                    place = self.controller.add.add_entity("Place", place_name=name)
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Could not find/create place:\n{e}")
            return

        if place is None:
            QMessageBox.critical(self, "Error", f"Could not find/create place '{name}'.")
            return

        known_types = fetch_association_types(self.controller)
        assoc_type_obj = find_or_create_association_type(
            self.controller, association_type, known_types
        )

        try:
            self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                place_id=place.place_id,
                association_type_id=assoc_type_obj.association_type_id if assoc_type_obj else None,
            )
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Could not link place:\n{e}")
            return

        self._reload_and_refresh()
        self._refresh_place_completers()
        self.new_place_edit.clear()
        self.new_place_assoc_edit.clear()
        self._selected_place_id = None

    def _edit_place(self, row):
        assoc_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        current_type = self.places_table.item(row, 1).text()
        known_types = [t.type_name for t in fetch_association_types(self.controller)]

        dialog = _EditAssociationTypeDialog(current_type, known_types, self)
        if dialog.exec() != QDialog.Accepted:
            return
        new_type_name = dialog.value()
        if not new_type_name:
            show_status_message(self, "Please enter the relationship type.")
            return

        assoc_type_obj = find_or_create_association_type(
            self.controller, new_type_name, fetch_association_types(self.controller)
        )
        success = self.controller.update.update_entity(
            "PlaceAssociation",
            assoc_id,
            association_type_id=assoc_type_obj.association_type_id if assoc_type_obj else None,
        )
        if not success:
            QMessageBox.critical(self, "Error", "Could not update relationship type.")
            return
        self._reload_and_refresh()

    def _remove_place(self, row):
        # assoc_id is always a PlaceAssociation.association_id -- _load_places
        # reads exclusively through the PlaceAssociation table -- so a single
        # delete by that id is always the right call (delete_entity itself
        # never raises; it returns False on failure).
        assoc_id = self.places_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        if not self.controller.delete.delete_entity("PlaceAssociation", assoc_id):
            QMessageBox.critical(self, "Error", "Could not unlink place.")
            return
        self._reload_and_refresh()
        # Removing a PlaceAssociation doesn't delete the Place or association
        # type themselves, so the completers' data is unaffected here.

    def _add_award(self):
        name = self.new_award_edit.text().strip()
        if not name:
            return
        try:
            award = self.controller.get.get_entity_object("Award", award_name=name)
            if award is None:
                award = self.controller.add.add_entity("Award", award_name=name)
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Could not find/create award:\n{e}")
            return
        try:
            self.controller.add.add_entity(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
                award_id=award.award_id,
            )
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Could not link award:\n{e}")
            return
        self._reload_and_refresh()
        self.new_award_edit.clear()

    def _remove_award(self, row):
        # assoc_id is always an AwardAssociation.association_id, per the same
        # reasoning as _remove_place.
        assoc_id = self.awards_table.item(row, 0).data(Qt.UserRole)
        if assoc_id is None:
            return
        if not self.controller.delete.delete_entity("AwardAssociation", assoc_id):
            QMessageBox.critical(self, "Error", "Could not unlink award.")
            return
        self._reload_and_refresh()

    def _on_place_text_edited(self, _text):
        # Any manual edit invalidates a previously-selected existing place;
        # fall back to name-based lookup/create in _add_place.
        self._selected_place_id = None

    def _on_place_completion_selected(self, text):
        model = self._place_completer.model()
        for row in range(model.rowCount()):
            item = model.item(row)
            if item.text() == text:
                self._selected_place_id = item.data(Qt.UserRole)
                # Show the clean place name in the box, not the "(type, region)" suffix
                self.new_place_edit.setText(item.data(Qt.UserRole + 1))
                return
        self._selected_place_id = None

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except SQLAlchemyError as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _refresh_place_completers(self):
        """Refresh the place/association-type autocomplete data in place.

        Only place add/remove can introduce a new place or association type,
        so this is only called from those paths, not from award add/remove.
        Reuses the existing completer objects (swapping their models) instead
        of recreating QCompleters, so no signal reconnection is needed.
        """
        self._place_completer.setModel(_build_place_completer_model(self.controller))
        assoc_model = self._assoc_type_completer.model()
        if isinstance(assoc_model, QStringListModel):
            assoc_model.setStringList(
                [t.type_name for t in fetch_association_types(self.controller)]
            )
