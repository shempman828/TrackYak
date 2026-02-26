"""
artist_alias_dialog.py

A robust alias management dialog for artists. Supports:
  - Viewing all aliases with their types
  - Adding new aliases
  - Editing existing aliases (name and type)
  - Deleting aliases
  - Swapping an alias with the artist's primary name
    (the old primary name is automatically saved as an alias)

Usage:
    dialog = ArtistAliasDialog(controller, artist)
    if dialog.exec() == QDialog.Accepted:
        # changes have already been persisted via the controller
        pass
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Alias types as defined in the ArtistAlias table comment
ALIAS_TYPES = [
    "",  # blank = unspecified
    "Legal Name",
    "Stylized Name",
    "Project Name",
    "Persona",
    "Birth Name",
    "Former Name",
    "Localized Name",
    "Romanized Name",
    "Phonetic Name",
    "Nickname",
    "Other",
]

# Column indices for the table
COL_NAME = 0
COL_TYPE = 1
COL_ACTIONS = 2


class AliasRowWidget(QWidget):
    """Inline action buttons for a table row."""

    def __init__(self, edit_cb, delete_cb, swap_cb, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        btn_edit = QPushButton("Edit")
        btn_edit.setFixedWidth(50)
        btn_edit.clicked.connect(edit_cb)

        btn_swap = QPushButton("↕ Use as Name")
        btn_swap.setToolTip("Swap this alias with the artist's primary name")
        btn_swap.clicked.connect(swap_cb)

        btn_delete = QPushButton("✕")
        btn_delete.setFixedWidth(28)
        btn_delete.setToolTip("Delete this alias")
        btn_delete.setStyleSheet("color: #cc4444;")
        btn_delete.clicked.connect(delete_cb)

        layout.addWidget(btn_edit)
        layout.addWidget(btn_swap)
        layout.addStretch()
        layout.addWidget(btn_delete)


# ---------------------------------------------------------------------------
# Add / Edit alias sub-dialog
# ---------------------------------------------------------------------------


class AliasEditDialog(QDialog):
    """Small dialog for entering / editing a single alias."""

    def __init__(self, alias_name: str = "", alias_type: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alias" if not alias_name else "Edit Alias")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit(alias_name)
        self.name_edit.setPlaceholderText("e.g. Marshall Mathers")
        form.addRow("Alias Name:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(ALIAS_TYPES)
        if alias_type in ALIAS_TYPES:
            self.type_combo.setCurrentText(alias_type)
        form.addRow("Alias Type:", self.type_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Alias name cannot be empty.")
            return
        self.accept()

    @property
    def alias_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def alias_type(self) -> str:
        return self.type_combo.currentText()


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------


class ArtistAliasDialog(QDialog):
    """
    Full-featured alias management dialog for an Artist.

    Parameters
    ----------
    controller : object
        Application controller with:
            controller.get.get_entity_object("ArtistAlias", alias_id=...)
            controller.get.get_all_entities("ArtistAlias", artist_id=...)
            controller.add.add_entity("ArtistAlias", artist_id=..., alias_name=..., alias_type=...)
            controller.update.update_entity("ArtistAlias", alias_id, alias_name=..., alias_type=...)
            controller.update.update_entity("Artist", artist_id, artist_name=...)
            controller.delete.delete_entity("ArtistAlias", alias_id)
    artist : Artist ORM object
        The artist whose aliases are being managed.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist

        self.setWindowTitle(f"Manage Aliases — {artist.artist_name}")
        self.setMinimumSize(620, 440)
        self.setModal(True)

        self._init_ui()
        self._load_aliases()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # --- Header ---
        header = QLabel(f"<b>Artist:</b> {self.artist.artist_name}")
        header.setStyleSheet("font-size: 14px; padding: 4px 0;")
        root.addWidget(header)

        # --- Alias table ---
        grp = QGroupBox("Aliases")
        grp_layout = QVBoxLayout(grp)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Alias Name", "Type", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(
            COL_NAME, QHeaderView.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            COL_TYPE, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            COL_ACTIONS, QHeaderView.Fixed
        )
        self.table.setColumnWidth(COL_ACTIONS, 200)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        grp_layout.addWidget(self.table)
        root.addWidget(grp)

        # --- Add alias button ---
        add_btn = QPushButton("＋  Add New Alias")
        add_btn.clicked.connect(self._add_alias)
        root.addWidget(add_btn, alignment=Qt.AlignLeft)

        # --- Dialog buttons ---
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.accept)  # Close = accept (changes already saved)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _load_aliases(self):
        """Populate the table from the database."""
        self.table.setRowCount(0)
        try:
            aliases = self.controller.get.get_all_entities(
                "ArtistAlias", artist_id=self.artist.artist_id
            )
        except Exception as e:
            logger.error(f"Failed to load aliases: {e}")
            aliases = []

        for alias in aliases:
            self._append_row(alias.alias_id, alias.alias_name, alias.alias_type or "")

    def _append_row(self, alias_id: int, alias_name: str, alias_type: str):
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(alias_name)
        name_item.setData(Qt.UserRole, alias_id)
        self.table.setItem(row, COL_NAME, name_item)

        type_item = QTableWidgetItem(alias_type)
        self.table.setItem(row, COL_TYPE, type_item)

        actions = AliasRowWidget(
            edit_cb=lambda checked=False, r=row: self._edit_alias(r),
            delete_cb=lambda checked=False, r=row: self._delete_alias(r),
            swap_cb=lambda checked=False, r=row: self._swap_alias(r),
        )
        self.table.setCellWidget(row, COL_ACTIONS, actions)
        self.table.setRowHeight(row, 38)

    def _row_alias_id(self, row: int) -> int:
        return self.table.item(row, COL_NAME).data(Qt.UserRole)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _add_alias(self):
        dlg = AliasEditDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        try:
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
            logger.info(
                f"Added alias '{dlg.alias_name}' to artist {self.artist.artist_id}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{e}")
            logger.error(f"Failed to add alias: {e}")
            return

        self._load_aliases()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        current_name = self.table.item(row, COL_NAME).text()
        current_type = self.table.item(row, COL_TYPE).text()

        dlg = AliasEditDialog(current_name, current_type, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        try:
            self.controller.update.update_entity(
                "ArtistAlias",
                alias_id,
                alias_name=dlg.alias_name,
                alias_type=dlg.alias_type or None,
            )
            logger.info(f"Updated alias {alias_id} → '{dlg.alias_name}'")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not update alias:\n{e}")
            logger.error(f"Failed to update alias {alias_id}: {e}")
            return

        self._load_aliases()

    def _delete_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, COL_NAME).text()

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
            logger.info(f"Deleted alias {alias_id} ('{alias_name}')")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not delete alias:\n{e}")
            logger.error(f"Failed to delete alias {alias_id}: {e}")
            return

        self._load_aliases()

    def _swap_alias(self, row: int):
        """
        Promote the selected alias to the artist's primary name.
        The current primary name is demoted to an alias of the same type
        (defaulting to 'Former Name' so it is clearly labelled).
        """
        alias_id = self._row_alias_id(row)
        new_primary = self.table.item(row, COL_NAME).text()
        alias_type = self.table.item(row, COL_TYPE).text()
        old_primary = self.artist.artist_name

        if new_primary == old_primary:
            QMessageBox.information(
                self, "No Change", "That alias is already the artist's primary name."
            )
            return

        reply = QMessageBox.question(
            self,
            "Swap Artist Name",
            f"This will:\n\n"
            f"  • Set the primary name to: <b>{new_primary}</b>\n"
            f"  • Save the current name (<b>{old_primary}</b>) as an alias\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            # 1. Remove the alias that is being promoted (its name slot is
            #    needed for the artist's primary name field).
            self.controller.delete.delete_entity("ArtistAlias", alias_id)

            # 2. Update the artist's primary name.
            self.controller.update.update_entity(
                "Artist", self.artist.artist_id, artist_name=new_primary
            )
            self.artist.artist_name = new_primary  # keep local object in sync

            # 3. Save the old primary name as an alias.
            save_type = "Former Name" if not alias_type else alias_type
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=old_primary,
                alias_type=save_type,
            )

            logger.info(
                f"Swapped artist name: '{old_primary}' → '{new_primary}' "
                f"(old name saved as '{save_type}' alias)"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not swap artist name:\n{e}")
            logger.error(f"Failed to swap artist name: {e}")
            return

        # Update the header label to reflect the new name
        self._refresh_header()
        self._load_aliases()

    def _refresh_header(self):
        """Refresh the header label and window title after a name change."""
        self.setWindowTitle(f"Manage Aliases — {self.artist.artist_name}")
        # Find and update the QLabel header
        for child in self.findChildren(QLabel):
            if "<b>Artist:</b>" in child.text():
                child.setText(f"<b>Artist:</b> {self.artist.artist_name}")
                break
