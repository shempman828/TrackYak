"""
Global manager for TagType rows: rename, describe, add, and delete the
user-defined tag categories (Vibe, Era, Religion, ...) shared across all
artists, independent of any single artist's edit dialog.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)
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
    editing, add, delete, and Move Up/Down reordering. The "# Artists"
    column counts distinct artists carrying any tag of that type (see
    TagManagerDialog for per-tag artist counts).

    Row order (and the order the Tags tab lays out its per-category
    sections in) follows `sort_order`, with `type_name` as a tiebreak --
    every row shares the default `sort_order=0` until the user reorders,
    so nothing changes visibly until Move Up/Down is used at least once.
    """

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
        self._table.itemSelectionChanged.connect(self._update_move_buttons)
        return self._table

    def _build_extra_buttons(self, btn_row) -> None:
        self._move_up_btn = QPushButton("Move Up")
        self._move_up_btn.setToolTip("Move the selected tag type earlier in the category order")
        self._move_up_btn.clicked.connect(lambda: self._move(-1))
        btn_row.addWidget(self._move_up_btn)

        self._move_down_btn = QPushButton("Move Down")
        self._move_down_btn.setToolTip("Move the selected tag type later in the category order")
        self._move_down_btn.clicked.connect(lambda: self._move(1))
        btn_row.addWidget(self._move_down_btn)

        self._update_move_buttons()

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
            self._current_types = sorted(
                self.controller.get.get_all_entities("TagType") or [],
                key=lambda t: (t.sort_order, t.type_name.lower()),
            )
            counts = self._fetch_counts()

            self._table.setRowCount(len(self._current_types))
            for row, t in enumerate(self._current_types):
                name_item = QTableWidgetItem(t.type_name)
                name_item.setData(Qt.UserRole, t.tag_type_id)
                self._table.setItem(row, NAME_COL, name_item)

                self._table.setItem(row, DESC_COL, QTableWidgetItem(t.description or ""))

                count_item = QTableWidgetItem(str(counts.get(t.tag_type_id, 0)))
                count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, COUNT_COL, count_item)
        finally:
            self._table.blockSignals(False)
        self._update_move_buttons()

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

    # ── Add (overridden to append at the end of the category order) ───────

    def _add(self):
        name, ok = QInputDialog.getText(self, self._ADD_DIALOG_TITLE, self._ADD_DIALOG_PROMPT)
        name = name.strip()
        if not ok or not name:
            return

        existing = self.controller.get.get_entity_object("TagType", type_name=name)
        if existing:
            QMessageBox.warning(
                self, "Duplicate Name", f"A {self._ENTITY_LABEL} named '{name}' already exists."
            )
            return

        next_order = max((t.sort_order for t in self._current_types), default=-1) + 1
        self.controller.add.add_entity("TagType", type_name=name, sort_order=next_order)
        self._load()

    # ── Reordering ──────────────────────────────────────────────────────────

    def _update_move_buttons(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        single = len(rows) == 1
        row = rows[0] if single else -1
        self._move_up_btn.setEnabled(single and row > 0)
        self._move_down_btn.setEnabled(single and 0 <= row < self._table.rowCount() - 1)

    def _move(self, delta: int) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if len(rows) != 1:
            return
        row = rows[0]
        target = row + delta
        if not (0 <= target < len(self._current_types)):
            return

        types = self._current_types
        types[row], types[target] = types[target], types[row]
        for i, t in enumerate(types):
            self.controller.update.update_entity("TagType", t.tag_type_id, sort_order=i)

        self._load()
        self._table.selectRow(target)
