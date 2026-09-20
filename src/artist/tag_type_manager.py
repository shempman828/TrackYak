"""
Global manager for TagType rows: rename, describe, add, and delete the
user-defined tag categories (Vibe, Era, Religion, ...) shared across all
artists, independent of any single artist's edit dialog.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem
from sqlalchemy import func, select

from src.common.dialogs.lookup_manager_dialog import (
    COUNT_COL,
    DESC_COL,
    NAME_COL,
    BaseLookupManagerDialog,
)
from src.db.db_tables import ArtistTagAssociation, Tag


class TagTypeManagerDialog(BaseLookupManagerDialog):
    """Table of every TagType in the library with inline rename/description
    editing, add, and delete. The "# Artists" column counts distinct
    artists carrying any tag of that type (see TagManagerDialog for
    per-tag artist counts)."""

    _ENTITY_TYPE = "TagType"
    _ID_ATTR = "tag_type_id"
    _NAME_ATTR = "type_name"
    _DESC_ATTR = "description"
    _ENTITY_LABEL = "tag type"
    _NAME_EMPTY_LABEL = "Tag type name"
    _ADD_BUTTON_TEXT = "Add Tag Type"
    _ADD_DIALOG_TITLE = "Add Tag Type"
    _ADD_DIALOG_PROMPT = "Type name:"
    _DELETE_SELECT_FIRST_MSG = "Select one or more tag types first."
    _DELETE_DIALOG_TITLE = "Delete Tag Type(s)"
    _DELETE_INTRO = (
        "Delete the following tag type(s)? Every tag under them, and every "
        "artist's assignment to those tags, will be deleted too."
    )

    def __init__(self, controller, parent=None):
        super().__init__(controller, "Manage Tag Types", (560, 480), parent)

    def _build_content_widget(self):
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "Description", "# Artists"])
        self._table.horizontalHeader().setSectionResizeMode(NAME_COL, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(DESC_COL, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(COUNT_COL, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        return self._table

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        return self._safe_fetch_counts(
            select(Tag.tag_type_id, func.count(func.distinct(ArtistTagAssociation.artist_id)))
            .select_from(Tag)
            .join(ArtistTagAssociation, ArtistTagAssociation.tag_id == Tag.tag_id)
            .group_by(Tag.tag_type_id)
        )

    def _load(self):
        self._table.blockSignals(True)
        try:
            types = sorted(
                self.controller.get.get_all_entities("TagType") or [],
                key=lambda t: t.type_name.lower(),
            )
            counts = self._fetch_counts()

            self._table.setRowCount(len(types))
            for row, t in enumerate(types):
                name_item = QTableWidgetItem(t.type_name)
                name_item.setData(Qt.UserRole, t.tag_type_id)
                self._table.setItem(row, NAME_COL, name_item)

                self._table.setItem(row, DESC_COL, QTableWidgetItem(t.description or ""))

                count_item = QTableWidgetItem(str(counts.get(t.tag_type_id, 0)))
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
            entries.append((name_item.data(Qt.UserRole), name_item.text(), count_item.text()))
        return entries

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        name_item = self._table.item(row, NAME_COL)
        tag_type_id = name_item.data(Qt.UserRole)

        if item.column() == NAME_COL:
            self._validate_and_rename(tag_type_id, item.text())
        elif item.column() == DESC_COL:
            self._save_description(tag_type_id, item.text())
