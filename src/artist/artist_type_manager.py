"""
Global manager for ArtistType rows: rename, describe, add, and delete the
canonical role vocabulary shared across all artists (Composer, Producer,
Pianist, ...), independent of any single artist's edit dialog.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)
from sqlalchemy import func, select

from src.common.lookup_manager_dialog import (
    COUNT_COL,
    DESC_COL,
    NAME_COL,
    BaseLookupManagerDialog,
)
from src.db.db_tables import ArtistTypeAssociation


class ArtistTypeManagerDialog(BaseLookupManagerDialog):
    """Table of every ArtistType in the library with inline rename/description
    editing, add, and delete."""

    _ENTITY_TYPE = "ArtistType"
    _ID_ATTR = "artist_type_id"
    _NAME_ATTR = "type_name"
    _DESC_ATTR = "type_description"
    _ENTITY_LABEL = "type"
    _NAME_EMPTY_LABEL = "Type name"
    _ADD_BUTTON_TEXT = "Add Type"
    _ADD_DIALOG_TITLE = "Add Artist Type"
    _ADD_DIALOG_PROMPT = "Type name:"
    _DELETE_SELECT_FIRST_MSG = "Select one or more types first."
    _DELETE_DIALOG_TITLE = "Delete Artist Type(s)"
    _DELETE_INTRO = (
        "Delete the following type(s)? Any artists carrying them will simply "
        "lose that type."
    )

    def __init__(self, controller, parent=None):
        super().__init__(controller, "Manage Artist Types", (560, 480), parent)

    def _build_content_widget(self):
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "Description", "# Artists"])
        self._table.horizontalHeader().setSectionResizeMode(
            NAME_COL, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            DESC_COL, QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            COUNT_COL, QHeaderView.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        return self._table

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        return self._safe_fetch_counts(
            select(ArtistTypeAssociation.artist_type_id, func.count()).group_by(
                ArtistTypeAssociation.artist_type_id
            )
        )

    def _load(self):
        self._table.blockSignals(True)
        try:
            types = sorted(
                self.controller.get.get_all_entities("ArtistType") or [],
                key=lambda t: t.type_name.lower(),
            )
            counts = self._fetch_counts()

            self._table.setRowCount(len(types))
            for row, t in enumerate(types):
                name_item = QTableWidgetItem(t.type_name)
                name_item.setData(Qt.UserRole, t.artist_type_id)
                self._table.setItem(row, NAME_COL, name_item)

                self._table.setItem(
                    row, DESC_COL, QTableWidgetItem(t.type_description or "")
                )

                count_item = QTableWidgetItem(str(counts.get(t.artist_type_id, 0)))
                count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, COUNT_COL, count_item)
        finally:
            self._table.blockSignals(False)

    def _selected_entries(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        entries = []
        for row in rows:
            name_item = self._table.item(row, NAME_COL)
            count_item = self._table.item(row, COUNT_COL)
            entries.append(
                (name_item.data(Qt.UserRole), name_item.text(), count_item.text())
            )
        return entries

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        name_item = self._table.item(row, NAME_COL)
        artist_type_id = name_item.data(Qt.UserRole)

        if item.column() == NAME_COL:
            self._validate_and_rename(artist_type_id, item.text())
        elif item.column() == DESC_COL:
            self._save_description(artist_type_id, item.text())
