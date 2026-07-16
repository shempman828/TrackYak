"""
entity_alias_tab.py

Generic alias-management table embeddable in an entity's edit dialog.
Works for any `<Model>Alias` table with `alias_id`, `alias_name`, and a
`<id_field>` FK column, e.g. PublisherAlias, GenreAlias.

Aliases resolve to their entity both when merging duplicates (the
merged-away entity's name is kept as an alias automatically, see
MergeDB._preserve_alias_on_merge) and when importing/adding tracks (an
alias name resolves to the canonical entity instead of creating a
duplicate, see GetFromDB.resolve_entity_or_alias).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class EntityAliasEditDialog(QDialog):
    """Small dialog for entering or editing a single alias name."""

    def __init__(self, alias_name: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Alias" if not alias_name else "Edit Alias")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(alias_name)
        if placeholder:
            self.name_edit.setPlaceholderText(placeholder)
        form.addRow("Alias Name:", self.name_edit)
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


class EntityAliasRowWidget(QWidget):
    """Compact Edit / ✕ buttons rendered inside a table cell."""

    def __init__(self, edit_cb, delete_cb, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        btn_edit = QPushButton("Edit")
        btn_edit.setFixedWidth(46)
        btn_edit.setFlat(True)
        btn_edit.setProperty("rowAction", True)
        btn_edit.clicked.connect(edit_cb)

        btn_delete = QPushButton("✕")
        btn_delete.setFixedWidth(26)
        btn_delete.setFlat(True)
        btn_delete.setToolTip("Delete this alias")
        btn_delete.setProperty("rowAction", True)
        btn_delete.setProperty("danger", True)
        btn_delete.clicked.connect(delete_cb)

        layout.addWidget(btn_edit)
        layout.addStretch()
        layout.addWidget(btn_delete)


class EntityAliasesTab(QWidget):
    """Table of an entity's aliases with Add / Edit / Delete actions."""

    def __init__(
        self,
        controller,
        entity,
        model_name: str,
        id_field: str,
        placeholder: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.entity = entity
        self.alias_model_name = f"{model_name}Alias"
        self.id_field = id_field
        self.placeholder = placeholder
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Alias Name", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        layout.addWidget(self.table)

        add_btn = QPushButton("＋  Add Alias")
        add_btn.setFixedHeight(28)
        add_btn.clicked.connect(self._add_alias)
        layout.addWidget(add_btn, alignment=Qt.AlignLeft)

    def load(self, entity):
        self.entity = entity
        self._reload_table()

    def _entity_id(self):
        return getattr(self.entity, self.id_field)

    def _reload_table(self):
        self.table.setRowCount(0)
        try:
            aliases = self.controller.get.get_all_entities(
                self.alias_model_name, **{self.id_field: self._entity_id()}
            )
        except Exception:
            logger.exception(
                "EntityAliasesTab: failed to fetch %s for %s=%s",
                self.alias_model_name,
                self.id_field,
                self._entity_id(),
            )
            aliases = []

        for alias in aliases:
            self._append_row(alias.alias_id, alias.alias_name)

    def _append_row(self, alias_id: int, alias_name: str):
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(alias_name)
        name_item.setData(Qt.UserRole, alias_id)
        self.table.setItem(row, 0, name_item)

        actions = EntityAliasRowWidget(
            edit_cb=lambda checked=False, r=row: self._edit_alias(r),
            delete_cb=lambda checked=False, r=row: self._delete_alias(r),
        )
        self.table.setCellWidget(row, 1, actions)
        self.table.setRowHeight(row, 32)

    def _row_alias_id(self, row: int) -> int:
        return self.table.item(row, 0).data(Qt.UserRole)

    def _add_alias(self):
        dlg = EntityAliasEditDialog(placeholder=self.placeholder, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.controller.add.add_entity(
                self.alias_model_name,
                alias_name=dlg.alias_name,
                **{self.id_field: self._entity_id()},
            )
        except Exception as exc:
            logger.exception("EntityAliasesTab: failed to add alias %r", dlg.alias_name)
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{exc}")
            return
        self._reload_table()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        dlg = EntityAliasEditDialog(
            alias_name=alias_name, placeholder=self.placeholder, parent=self
        )
        if dlg.exec() != QDialog.Accepted:
            return
        success = self.controller.update.update_entity(
            self.alias_model_name, alias_id, alias_name=dlg.alias_name
        )
        if not success:
            show_status_message(
                self,
                f"Could not rename alias to '{dlg.alias_name}'. "
                "That name may already be in use.",
            )
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
            self.controller.delete.delete_entity(self.alias_model_name, alias_id)
        except Exception as exc:
            logger.exception("EntityAliasesTab: failed to delete alias_id=%s", alias_id)
            QMessageBox.critical(self, "Error", f"Could not delete alias:\n{exc}")
            return
        self._reload_table()
