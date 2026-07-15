# ══════════════════════════════════════════════════════════════════════════════
# Tab: Aliases  (embedded, no separate dialog window)
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.artist_alias_dialog import AliasEditDialog
from src.logger_config import logger


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
        logger.debug(
            "AliasesTab.load: artist_id=%s name=%r",
            artist.artist_id,
            artist.artist_name,
        )
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
            logger.debug(
                "AliasesTab._reload_table: fetched %d aliases for artist_id=%s",
                len(aliases),
                self.artist.artist_id,
            )
        except Exception:
            logger.exception(
                "AliasesTab._reload_table: failed to fetch aliases for artist_id=%s",
                self.artist.artist_id,
            )
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
        logger.debug(
            "AliasesTab._add_alias: opening dialog for artist_id=%s",
            self.artist.artist_id,
        )
        dlg = AliasEditDialog(extra_types=self._existing_types(), parent=self)
        # FIX: was `dlg.accepted` (always int 1) — must use QDialog.Accepted
        if dlg.exec() != QDialog.Accepted:
            logger.debug("AliasesTab._add_alias: dialog cancelled")
            return
        logger.debug(
            "AliasesTab._add_alias: accepted name=%r type=%r",
            dlg.alias_name,
            dlg.alias_type,
        )
        try:
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
            logger.info(
                "AliasesTab._add_alias: added alias %r for artist_id=%s",
                dlg.alias_name,
                self.artist.artist_id,
            )
        except Exception as exc:
            logger.exception(
                "AliasesTab._add_alias: failed to add alias %r", dlg.alias_name
            )
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{exc}")
            return
        self._reload_table()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        alias_type = self.table.item(row, 1).text()
        logger.debug(
            "AliasesTab._edit_alias: row=%d alias_id=%s name=%r type=%r",
            row,
            alias_id,
            alias_name,
            alias_type,
        )
        dlg = AliasEditDialog(
            alias_name=alias_name,
            alias_type=alias_type,
            extra_types=self._existing_types(),
            parent=self,
        )
        # FIX: was `dlg.accepted` (always int 1) — must use QDialog.Accepted
        if dlg.exec() != QDialog.Accepted:
            logger.debug(
                "AliasesTab._edit_alias: dialog cancelled for alias_id=%s", alias_id
            )
            return
        logger.debug(
            "AliasesTab._edit_alias: accepted new name=%r type=%r",
            dlg.alias_name,
            dlg.alias_type,
        )
        try:
            success = self.controller.update.update_entity(
                "ArtistAlias",
                alias_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
        except Exception as exc:
            logger.exception(
                "AliasesTab._edit_alias: failed to update alias_id=%s", alias_id
            )
            QMessageBox.critical(self, "Error", f"Could not update alias:\n{exc}")
            return
        if not success:
            logger.warning(
                "AliasesTab._edit_alias: update rejected for alias_id=%s (likely a "
                "duplicate alias name)",
                alias_id,
            )
            QMessageBox.warning(
                self,
                "Could Not Update Alias",
                f"Could not rename alias to '{dlg.alias_name}'. "
                "That name may already be in use.",
            )
            return
        logger.info(
            "AliasesTab._edit_alias: updated alias_id=%s to name=%r type=%r",
            alias_id,
            dlg.alias_name,
            dlg.alias_type,
        )
        self._reload_table()

    def _delete_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        logger.debug(
            "AliasesTab._delete_alias: row=%d alias_id=%s name=%r",
            row,
            alias_id,
            alias_name,
        )
        reply = QMessageBox.question(
            self,
            "Delete Alias",
            f"Delete alias <b>{alias_name}</b>?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            logger.debug(
                "AliasesTab._delete_alias: cancelled for alias_id=%s", alias_id
            )
            return
        try:
            self.controller.delete.delete_entity("ArtistAlias", alias_id)
            logger.info(
                "AliasesTab._delete_alias: deleted alias_id=%s name=%r",
                alias_id,
                alias_name,
            )
        except Exception as exc:
            logger.exception(
                "AliasesTab._delete_alias: failed to delete alias_id=%s", alias_id
            )
            QMessageBox.critical(self, "Error", f"Could not delete alias:\n{exc}")
            return
        self._reload_table()

    def _swap_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        new_primary = self.table.item(row, 0).text()
        alias_type = self.table.item(row, 1).text()
        old_primary = self.artist.artist_name
        logger.debug(
            "AliasesTab._swap_alias: row=%d alias_id=%s old=%r new=%r",
            row,
            alias_id,
            old_primary,
            new_primary,
        )

        if new_primary == old_primary:
            logger.debug(
                "AliasesTab._swap_alias: no-op, alias already matches primary name"
            )
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
            logger.debug("AliasesTab._swap_alias: cancelled")
            return

        try:
            # Attempt the rename first, before touching the alias row: if the
            # new name collides with another artist we want to fail loudly
            # and leave both the alias and the primary name untouched.
            success = self.controller.update.update_entity(
                "Artist", self.artist.artist_id, artist_name=new_primary
            )
            if not success:
                logger.warning(
                    "AliasesTab._swap_alias: rename rejected for artist_id=%s -> %r "
                    "(likely a duplicate artist name)",
                    self.artist.artist_id,
                    new_primary,
                )
                QMessageBox.warning(
                    self,
                    "Could Not Swap Name",
                    f"Could not set primary name to '{new_primary}'. "
                    "Another artist may already have that name.",
                )
                return

            self.controller.delete.delete_entity("ArtistAlias", alias_id)
            self.artist.artist_name = new_primary
            save_type = alias_type or "Former Name"
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=old_primary,
                alias_type=save_type,
            )
            logger.info(
                "AliasesTab._swap_alias: swapped artist_id=%s primary %r -> %r (old saved as type=%r)",
                self.artist.artist_id,
                old_primary,
                new_primary,
                save_type,
            )
        except Exception as exc:
            logger.exception(
                "AliasesTab._swap_alias: failed during swap for artist_id=%s",
                self.artist.artist_id,
            )
            QMessageBox.critical(self, "Error", f"Could not swap name:\n{exc}")
            return

        self._reload_table()
