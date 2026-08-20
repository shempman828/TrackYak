"""
global_merge_alias_tab.py

Generic, cross-entity alias-management table for the unified "Manage
Aliases…" dialog (see docs/specs/split_and_merge_aliases.md). Unlike
EntityAliasesTab (entity_alias_tab.py), which shows one entity's own
aliases from inside that entity's edit dialog, this shows every alias row
in a `<Model>Alias` table at once, with an Add row that resolves its
target to an existing entity via a search completer rather than
free-typing (an alias is only meaningful pointing at an entity that
already exists). Reusable across Genre/Artist/Publisher/Role -- the shape
of GenreAlias/ArtistAlias/PublisherAlias/RoleAlias is identical.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
from sqlalchemy.exc import SQLAlchemyError

from src.common.entity_completer_edit import build_entity_search_widget
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class _MergeAliasEditDialog(QDialog):
    """Popup for editing an existing merge-alias row's name and/or target
    entity."""

    def __init__(self, controller, model_name, name_field, id_field, alias_name, target_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Alias")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(alias_name)
        form.addRow("Alias Name:", self.name_edit)

        self.target_edit = build_entity_search_widget(
            controller, model_name, name_field, id_field, f"Search {model_name.lower()}s…"
        )
        current = controller.get.get_entity_object(model_name, **{id_field: target_id})
        if current is not None:
            self.target_edit.setText(getattr(current, name_field))
        form.addRow(f"Resolves To ({model_name}):", self.target_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Alias name cannot be empty.")
            return
        if self.target_edit.matched_id() is None:
            QMessageBox.warning(
                self, "Validation", "Pick an existing entity from the search results."
            )
            return
        self.accept()

    @property
    def alias_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def target_id(self):
        return self.target_edit.matched_id()


class GlobalMergeAliasTab(QWidget):
    """Table of every `<model_name>Alias` row, with add/edit/delete."""

    def __init__(self, controller, model_name: str, name_field: str, id_field: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.model_name = model_name
        self.name_field = name_field
        self.id_field = id_field
        self.alias_model_name = f"{model_name}Alias"
        self._build_ui()
        self.reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(
            QLabel(
                f"An alias name resolves to its {self.model_name.lower()} on import "
                "or manual add, instead of creating a duplicate."
            )
        )

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Alias name")
        add_row.addWidget(self.name_edit, 1)

        self.target_edit = build_entity_search_widget(
            controller=self.controller,
            model_name=self.model_name,
            name_field=self.name_field,
            id_field=self.id_field,
            placeholder_text=f"Resolves to this {self.model_name.lower()}…",
        )
        add_row.addWidget(self.target_edit, 1)

        self.add_btn = QPushButton("+ Add Alias")
        self.add_btn.clicked.connect(self._add_alias)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Alias Name", f"Resolves To ({self.model_name})", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 110)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        layout.addWidget(self.table)

    def reload(self):
        self.table.setRowCount(0)
        try:
            rows = self.controller.get.get_all_entities(self.alias_model_name) or []
        except SQLAlchemyError:
            logger.exception("GlobalMergeAliasTab: failed to load %s", self.alias_model_name)
            rows = []
        for row in sorted(rows, key=lambda r: r.alias_name.lower()):
            self._append_row(row)

    def _target_display(self, row) -> str:
        target = getattr(row, self.model_name.lower(), None)
        return getattr(target, self.name_field, "") if target is not None else "(deleted)"

    def _append_row(self, row):
        r = self.table.rowCount()
        self.table.insertRow(r)

        name_item = QTableWidgetItem(row.alias_name)
        name_item.setData(Qt.UserRole, row.alias_id)
        self.table.setItem(r, 0, name_item)

        target_id = getattr(row, self.id_field)
        target_item = QTableWidgetItem(self._target_display(row))
        target_item.setData(Qt.UserRole, target_id)
        self.table.setItem(r, 1, target_item)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(4)
        edit_btn = QPushButton("Edit")
        edit_btn.setFlat(True)
        edit_btn.clicked.connect(lambda checked=False, rr=r: self._edit_row(rr))
        delete_btn = QPushButton("✕")
        delete_btn.setFlat(True)
        delete_btn.setToolTip("Delete this alias")
        delete_btn.clicked.connect(lambda checked=False, rr=r: self._delete_row(rr))
        actions_layout.addWidget(edit_btn)
        actions_layout.addWidget(delete_btn)
        actions_layout.addStretch()
        self.table.setCellWidget(r, 2, actions)
        self.table.setRowHeight(r, 32)

    def _add_alias(self):
        alias_name = self.name_edit.text().strip()
        target_id = self.target_edit.matched_id()
        if not alias_name:
            show_status_message(self, "Enter an alias name.")
            return
        if target_id is None:
            show_status_message(self, f"Pick an existing {self.model_name.lower()} to resolve to.")
            return
        try:
            self.controller.add.add_entity(
                self.alias_model_name, alias_name=alias_name, **{self.id_field: target_id}
            )
        except SQLAlchemyError as exc:
            logger.exception("GlobalMergeAliasTab: failed to add alias %r", alias_name)
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{exc}")
            return
        self.name_edit.clear()
        self.target_edit.reset()
        self.reload()

    def _row_alias_id(self, row: int) -> int:
        return self.table.item(row, 0).data(Qt.UserRole)

    def _row_target_id(self, row: int):
        return self.table.item(row, 1).data(Qt.UserRole)

    def _edit_row(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        target_id = self._row_target_id(row)
        dlg = _MergeAliasEditDialog(
            self.controller, self.model_name, self.name_field, self.id_field,
            alias_name, target_id, parent=self,
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        try:
            self.controller.update.update_entity(
                self.alias_model_name,
                alias_id,
                alias_name=dlg.alias_name,
                **{self.id_field: dlg.target_id},
            )
        except SQLAlchemyError as exc:
            logger.exception("GlobalMergeAliasTab: failed to update alias_id=%s", alias_id)
            QMessageBox.critical(self, "Error", f"Could not update alias:\n{exc}")
            return
        self.reload()

    def _delete_row(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        reply = QMessageBox.question(
            self, "Delete Alias", f"Delete alias <b>{alias_name}</b>?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.delete.delete_entity(self.alias_model_name, alias_id)
        except SQLAlchemyError as exc:
            logger.exception("GlobalMergeAliasTab: failed to delete alias_id=%s", alias_id)
            QMessageBox.critical(self, "Error", f"Could not delete alias:\n{exc}")
            return
        self.reload()
