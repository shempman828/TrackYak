"""
Global manager for Religion rows: rename, describe, set parent, add, and
delete the shared religion/denomination vocabulary usable across all artists,
independent of any single artist's edit dialog.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_tables import Artist

_NAME_COL = 0
_PARENT_COL = 1
_DESC_COL = 2
_COUNT_COL = 3


def _valid_parents(all_religions, religion):
    """Every religion except itself and its direct children -- doesn't walk
    the full descendant subtree, matching the same known limitation as
    genre_edit.get_valid_parents."""
    child_ids = {r.religion_id for r in all_religions if r.parent_id == religion.religion_id}
    return [
        r
        for r in all_religions
        if r.religion_id != religion.religion_id and r.religion_id not in child_ids
    ]


class ReligionManagerDialog(QDialog):
    """Table of every Religion in the library with inline rename/description
    editing, parent selection, add, and delete."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Manage Religions")
        self.resize(640, 480)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Parent", "Description", "# Artists"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _NAME_COL, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _PARENT_COL, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _DESC_COL, QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COUNT_COL, QHeaderView.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Religion")
        add_btn.clicked.connect(self._add_religion)
        btn_row.addWidget(add_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ── Loading ───────────────────────────────────────────────────────────

    def _fetch_counts(self) -> dict:
        try:
            rows = self.controller.get.session.execute(
                select(Artist.religion_id, func.count())
                .where(Artist.religion_id.isnot(None))
                .group_by(Artist.religion_id)
            ).all()
            return dict(rows)
        except SQLAlchemyError as e:
            logger.warning(f"Failed to fetch religion usage counts: {e}")
            return {}

    def _load(self):
        self._table.blockSignals(True)
        try:
            self._religions = sorted(
                self.controller.get.get_all_entities("Religion") or [],
                key=lambda r: r.religion_name.lower(),
            )
            counts = self._fetch_counts()

            self._table.setRowCount(len(self._religions))
            for row, r in enumerate(self._religions):
                name_item = QTableWidgetItem(r.religion_name)
                name_item.setData(Qt.UserRole, r.religion_id)
                self._table.setItem(row, _NAME_COL, name_item)

                self._table.setCellWidget(row, _PARENT_COL, self._build_parent_combo(r))

                self._table.setItem(
                    row, _DESC_COL, QTableWidgetItem(r.description or "")
                )

                count_item = QTableWidgetItem(str(counts.get(r.religion_id, 0)))
                count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, _COUNT_COL, count_item)
        finally:
            self._table.blockSignals(False)

    def _build_parent_combo(self, religion):
        combo = QComboBox()
        combo.addItem("(No parent)", None)
        for candidate in sorted(
            _valid_parents(self._religions, religion), key=lambda r: r.religion_name.lower()
        ):
            combo.addItem(candidate.religion_name, candidate.religion_id)
        idx = combo.findData(religion.parent_id)
        combo.setCurrentIndex(max(idx, 0))
        combo.currentIndexChanged.connect(
            lambda _idx, rid=religion.religion_id, cb=combo: self._on_parent_changed(rid, cb)
        )
        return combo

    def _on_parent_changed(self, religion_id, combo):
        self.controller.update.update_entity(
            "Religion", religion_id, parent_id=combo.currentData()
        )

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        name_item = self._table.item(row, _NAME_COL)
        religion_id = name_item.data(Qt.UserRole)

        if item.column() == _NAME_COL:
            new_name = item.text().strip()
            if not new_name:
                QMessageBox.warning(self, "Invalid Name", "Religion name cannot be empty.")
                self._load()
                return

            existing = self.controller.get.get_entity_object(
                "Religion", religion_name=new_name
            )
            if existing and existing.religion_id != religion_id:
                QMessageBox.warning(
                    self,
                    "Duplicate Name",
                    f"A religion named '{new_name}' already exists.",
                )
                self._load()
                return

            self.controller.update.update_entity(
                "Religion", religion_id, religion_name=new_name
            )

        elif item.column() == _DESC_COL:
            self.controller.update.update_entity(
                "Religion", religion_id, description=item.text().strip() or None
            )

    def _add_religion(self):
        name, ok = QInputDialog.getText(self, "Add Religion", "Name:")
        name = name.strip()
        if not ok or not name:
            return

        existing = self.controller.get.get_entity_object("Religion", religion_name=name)
        if existing:
            QMessageBox.warning(
                self, "Duplicate Name", f"A religion named '{name}' already exists."
            )
            return

        self.controller.add.add_entity("Religion", religion_name=name)
        self._load()

    def _delete_selected(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self, "Delete Religion", "Select one or more religions first."
            )
            return

        entries = []
        for row in rows:
            name_item = self._table.item(row, _NAME_COL)
            count_item = self._table.item(row, _COUNT_COL)
            entries.append(
                (name_item.data(Qt.UserRole), name_item.text(), count_item.text())
            )

        lines = [f"• {name} ({count} artist(s))" for _id, name, count in entries]
        reply = QMessageBox.question(
            self,
            "Delete Religion(s)",
            "Delete the following religion(s)? Any artists carrying them will simply "
            "lose that affiliation, and any child religions will lose their parent.\n\n"
            + "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for religion_id, _name, _count in entries:
            self.controller.delete.delete_entity("Religion", religion_id=religion_id)
        self._load()
