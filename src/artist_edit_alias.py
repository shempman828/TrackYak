# ══════════════════════════════════════════════════════════════════════════════
# Tab: Aliases  (embedded, no separate dialog window)
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.artist_alias_dialog import AliasEditDialog


class AliasesTab(QWidget):
    """
    Alias management embedded directly in the artist-edit tab.

    Displays a table of aliases with per-row Edit / Delete / Swap actions
    revealed on hover, and an Add button below the table.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        info = QLabel(
            "Aliases let an artist be discovered under multiple names. "
            "Changes are saved immediately."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Alias Name", "Type", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 180)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("""
            QTableWidget { border: 1px solid palette(mid); border-radius: 4px; }
            QTableWidget::item { padding: 4px 6px; }
        """)
        layout.addWidget(self.table)

        add_btn = QPushButton("＋  Add Alias")
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(self._add_alias)
        layout.addWidget(add_btn, alignment=Qt.AlignLeft)
        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API — called by the parent edit form
    # ------------------------------------------------------------------

    def load(self, artist):
        self.artist = artist
        self._reload_table()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reload_table(self):
        self.table.setRowCount(0)
        try:
            aliases = self.controller.get.get_all_entities(
                "ArtistAlias", artist_id=self.artist.artist_id
            )
        except Exception:
            aliases = []

        for alias in aliases:
            self._append_row(alias.alias_id, alias.alias_name, alias.alias_type or "")

    def _append_row(self, alias_id: int, alias_name: str, alias_type: str):
        from src.artist_alias_dialog import AliasRowWidget  # reuse action widget

        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(alias_name)
        name_item.setData(Qt.UserRole, alias_id)
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(alias_type))

        actions = AliasRowWidget(
            edit_cb=lambda checked=False, r=row: self._edit_alias(r),
            delete_cb=lambda checked=False, r=row: self._delete_alias(r),
            swap_cb=lambda checked=False, r=row: self._swap_alias(r),
        )
        self.table.setCellWidget(row, 2, actions)
        self.table.setRowHeight(row, 36)

    def _row_alias_id(self, row: int) -> int:
        return self.table.item(row, 0).data(Qt.UserRole)

    def _existing_types(self) -> list[str]:
        """Collect the distinct alias types already in the table for autocomplete."""
        types = set()
        for r in range(self.table.rowCount()):
            t = self.table.item(r, 1).text().strip()
            if t:
                types.add(t)
        return sorted(types)

    # ------------------------------------------------------------------
    # CRUD actions
    # ------------------------------------------------------------------

    def _add_alias(self):
        dlg = AliasEditDialog(extra_types=self._existing_types(), parent=self)
        if dlg.exec() != dlg.Accepted:
            return
        try:
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{e}")
            return
        self._reload_table()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        dlg = AliasEditDialog(
            alias_name=self.table.item(row, 0).text(),
            alias_type=self.table.item(row, 1).text(),
            extra_types=self._existing_types(),
            parent=self,
        )
        if dlg.exec() != dlg.Accepted:
            return
        try:
            self.controller.update.update_entity(
                "ArtistAlias",
                alias_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not update alias:\n{e}")
            return
        self._reload_table()

    def _delete_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        reply = QMessageBox.question(
            self,
            "Delete Alias",
            f"Delete alias <b>{alias_name}</b>?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.delete.delete_entity("ArtistAlias", alias_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not delete alias:\n{e}")
            return
        self._reload_table()

    def _swap_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        new_primary = self.table.item(row, 0).text()
        alias_type = self.table.item(row, 1).text()
        old_primary = self.artist.artist_name

        if new_primary == old_primary:
            QMessageBox.information(
                self, "No Change", "That alias is already the primary name."
            )
            return

        reply = QMessageBox.question(
            self,
            "Use as Primary Name",
            f"Set primary name to <b>{new_primary}</b>?<br>"
            f"Current name <b>{old_primary}</b> will be saved as an alias.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.controller.delete.delete_entity("ArtistAlias", alias_id)
            self.controller.update.update_entity(
                "Artist", self.artist.artist_id, artist_name=new_primary
            )
            self.artist.artist_name = new_primary
            save_type = alias_type or "Former Name"
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=old_primary,
                alias_type=save_type,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not swap name:\n{e}")
            return

        self._reload_table()
