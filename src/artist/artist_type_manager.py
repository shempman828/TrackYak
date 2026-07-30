"""
Global manager for ArtistType rows: rename, describe, add, and delete the
canonical role vocabulary shared across all artists (Composer, Producer,
Pianist, ...), independent of any single artist's edit dialog.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from src.db.db_tables import ArtistTypeAssociation

_NAME_COL = 0
_DESC_COL = 1
_COUNT_COL = 2


class ArtistTypeManagerDialog(QDialog):
    """Table of every ArtistType in the library with inline rename/description
    editing, add, and delete."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Manage Artist Types")
        self.resize(560, 480)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "Description", "# Artists"])
        self._table.horizontalHeader().setSectionResizeMode(
            _NAME_COL, QHeaderView.ResizeToContents
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
        add_btn = QPushButton("Add Type")
        add_btn.clicked.connect(self._add_type)
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
                select(
                    ArtistTypeAssociation.artist_type_id, func.count()
                ).group_by(ArtistTypeAssociation.artist_type_id)
            ).all()
            return dict(rows)
        except SQLAlchemyError as e:
            logger.warning(f"Failed to fetch artist-type usage counts: {e}")
            return {}

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
                self._table.setItem(row, _NAME_COL, name_item)

                self._table.setItem(
                    row, _DESC_COL, QTableWidgetItem(t.type_description or "")
                )

                count_item = QTableWidgetItem(str(counts.get(t.artist_type_id, 0)))
                count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, _COUNT_COL, count_item)
        finally:
            self._table.blockSignals(False)

    # ── Editing ───────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        row = item.row()
        name_item = self._table.item(row, _NAME_COL)
        artist_type_id = name_item.data(Qt.UserRole)

        if item.column() == _NAME_COL:
            new_name = item.text().strip()
            if not new_name:
                QMessageBox.warning(self, "Invalid Name", "Type name cannot be empty.")
                self._load()
                return

            existing = self.controller.get.get_entity_object(
                "ArtistType", type_name=new_name
            )
            if existing and existing.artist_type_id != artist_type_id:
                QMessageBox.warning(
                    self, "Duplicate Name", f"A type named '{new_name}' already exists."
                )
                self._load()
                return

            self.controller.update.update_entity(
                "ArtistType", artist_type_id, type_name=new_name
            )

        elif item.column() == _DESC_COL:
            self.controller.update.update_entity(
                "ArtistType", artist_type_id, type_description=item.text().strip() or None
            )

    def _add_type(self):
        name, ok = QInputDialog.getText(self, "Add Artist Type", "Type name:")
        name = name.strip()
        if not ok or not name:
            return

        existing = self.controller.get.get_entity_object("ArtistType", type_name=name)
        if existing:
            QMessageBox.warning(
                self, "Duplicate Name", f"A type named '{name}' already exists."
            )
            return

        self.controller.add.add_entity("ArtistType", type_name=name)
        self._load()

    def _delete_selected(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Delete Type", "Select one or more types first.")
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
            "Delete Artist Type(s)",
            "Delete the following type(s)? Any artists carrying them will simply "
            "lose that type.\n\n" + "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for artist_type_id, _name, _count in entries:
            self.controller.delete.delete_entity(
                "ArtistType", artist_type_id=artist_type_id
            )
        self._load()
