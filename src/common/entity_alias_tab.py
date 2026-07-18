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

Three optional hooks let a subclass cover richer alias models without
forking the whole widget:
  - extra_field: adds a second free-text column (e.g. an alias "type")
    with autocomplete, backed by an attribute of that name on the alias
    model. Override _extra_completions() to add fixed suggestions beyond
    the values already present in the table (see artist_edit_alias.py).
  - on_swap: adds a per-row "Use as Name" action that promotes an alias
    to the entity's primary name. Receives (alias_id, new_primary_name,
    extra_value) and should return True if the swap was performed (so the
    table reloads).
  - on_before_add: called with the proposed alias name just before it is
    saved. Return False to abort the add (e.g. because the name already
    belongs to another entity and the subclass handled it, such as
    offering a merge instead — see AliasesTab._check_existing_artist in
    artist_edit_alias.py). Return True to proceed as normal.
"""

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCompleter,
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

from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class EntityAliasEditDialog(QDialog):
    """
    Small dialog for entering or editing a single alias name, with an
    optional second free-text field (e.g. an alias "type") for entities
    that need per-alias metadata beyond the name.
    """

    def __init__(
        self,
        alias_name: str = "",
        placeholder: str = "",
        extra_field_label: Optional[str] = None,
        extra_value: str = "",
        extra_placeholder: str = "",
        extra_completions: Optional[list[str]] = None,
        extra_hint: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Add Alias" if not alias_name else "Edit Alias")
        self.setMinimumWidth(360 if extra_field_label else 340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(alias_name)
        if placeholder:
            self.name_edit.setPlaceholderText(placeholder)
        form.addRow("Alias Name:", self.name_edit)

        self.extra_edit = None
        if extra_field_label:
            self.extra_edit = QLineEdit(extra_value)
            if extra_placeholder:
                self.extra_edit.setPlaceholderText(extra_placeholder)
            self.extra_edit.setClearButtonEnabled(True)
            if extra_completions:
                completer = QCompleter(list(dict.fromkeys(extra_completions)), self)
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                completer.setFilterMode(Qt.MatchContains)
                completer.setCompletionMode(QCompleter.PopupCompletion)
                self.extra_edit.setCompleter(completer)
            form.addRow(f"{extra_field_label}:", self.extra_edit)

        layout.addLayout(form)

        if extra_field_label and extra_hint:
            hint = QLabel(f"<small><i>{extra_hint}</i></small>")
            hint.setWordWrap(True)
            layout.addWidget(hint)

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
    def extra_value(self) -> str:
        return self.extra_edit.text().strip() if self.extra_edit else ""


class EntityAliasRowWidget(QWidget):
    """
    Compact Edit / [Use as Name] / ✕ buttons rendered inside a table cell.
    The swap button only appears when swap_cb is provided.
    """

    def __init__(
        self,
        edit_cb,
        delete_cb,
        swap_cb=None,
        swap_label: str = "↕ Use as Name",
        swap_tooltip: str = "Promote this alias to the entity's primary name",
        parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        btn_edit = QPushButton("Edit")
        btn_edit.setFixedWidth(46)
        btn_edit.setFlat(True)
        btn_edit.setProperty("rowAction", True)
        btn_edit.clicked.connect(edit_cb)
        layout.addWidget(btn_edit)

        if swap_cb is not None:
            btn_swap = QPushButton(swap_label)
            btn_swap.setFlat(True)
            btn_swap.setToolTip(swap_tooltip)
            btn_swap.setProperty("rowAction", True)
            btn_swap.clicked.connect(swap_cb)
            layout.addWidget(btn_swap)

        btn_delete = QPushButton("✕")
        btn_delete.setFixedWidth(26)
        btn_delete.setFlat(True)
        btn_delete.setToolTip("Delete this alias")
        btn_delete.setProperty("rowAction", True)
        btn_delete.setProperty("danger", True)
        btn_delete.clicked.connect(delete_cb)

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
        extra_field: Optional[str] = None,
        extra_field_label: str = "Type",
        extra_field_placeholder: str = "",
        extra_field_hint: str = "",
        on_swap: Optional[Callable[[int, str, str], bool]] = None,
        on_before_add: Optional[Callable[[str], bool]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.entity = entity
        self.alias_model_name = f"{model_name}Alias"
        self.id_field = id_field
        self.placeholder = placeholder
        self.extra_field = extra_field
        self.extra_field_label = extra_field_label
        self.extra_field_placeholder = extra_field_placeholder
        self.extra_field_hint = extra_field_hint
        self.on_swap = on_swap
        self.on_before_add = on_before_add
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        headers = ["Alias Name"]
        if self.extra_field:
            headers.append(self.extra_field_label)
        headers.append("")
        actions_col = len(headers) - 1

        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        if self.extra_field:
            self.table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeToContents
            )
        self.table.horizontalHeader().setSectionResizeMode(actions_col, QHeaderView.Fixed)
        self.table.setColumnWidth(actions_col, 180 if self.on_swap else 90)
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
            extra_value = (
                (getattr(alias, self.extra_field, "") or "") if self.extra_field else None
            )
            self._append_row(alias.alias_id, alias.alias_name, extra_value)

    def _append_row(self, alias_id: int, alias_name: str, extra_value: Optional[str] = None):
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(alias_name)
        name_item.setData(Qt.UserRole, alias_id)
        self.table.setItem(row, 0, name_item)

        if self.extra_field:
            self.table.setItem(row, 1, QTableWidgetItem(extra_value or ""))

        actions = EntityAliasRowWidget(
            edit_cb=lambda checked=False, r=row: self._edit_alias(r),
            delete_cb=lambda checked=False, r=row: self._delete_alias(r),
            swap_cb=(lambda checked=False, r=row: self._swap_alias(r)) if self.on_swap else None,
        )
        self.table.setCellWidget(row, self.table.columnCount() - 1, actions)
        self.table.setRowHeight(row, 36 if self.on_swap else 32)

    def _row_alias_id(self, row: int) -> int:
        return self.table.item(row, 0).data(Qt.UserRole)

    def _row_extra_value(self, row: int) -> str:
        if not self.extra_field:
            return ""
        item = self.table.item(row, 1)
        return item.text().strip() if item else ""

    def _existing_extra_values(self) -> list[str]:
        """Distinct extra-field values already in the table, for autocomplete."""
        if not self.extra_field:
            return []
        values = {self._row_extra_value(r) for r in range(self.table.rowCount())}
        values.discard("")
        return sorted(values)

    def _extra_completions(self) -> list[str]:
        """
        Autocomplete suggestions offered in the extra-field edit dialog.
        Override to add fixed suggestions beyond values already in use
        (see artist_edit_alias.AliasesTab._extra_completions).
        """
        return self._existing_extra_values()

    def _add_alias(self):
        dlg = EntityAliasEditDialog(
            placeholder=self.placeholder,
            extra_field_label=self.extra_field_label if self.extra_field else None,
            extra_placeholder=self.extra_field_placeholder,
            extra_completions=self._extra_completions() if self.extra_field else None,
            extra_hint=self.extra_field_hint,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        if self.on_before_add and not self.on_before_add(dlg.alias_name):
            self._reload_table()
            return
        kwargs = {self.id_field: self._entity_id(), "alias_name": dlg.alias_name}
        if self.extra_field:
            kwargs[self.extra_field] = dlg.extra_value or None
        try:
            self.controller.add.add_entity(self.alias_model_name, **kwargs)
        except Exception as exc:
            logger.exception("EntityAliasesTab: failed to add alias %r", dlg.alias_name)
            QMessageBox.critical(self, "Error", f"Could not add alias:\n{exc}")
            return
        self._reload_table()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self.table.item(row, 0).text()
        dlg = EntityAliasEditDialog(
            alias_name=alias_name,
            placeholder=self.placeholder,
            extra_field_label=self.extra_field_label if self.extra_field else None,
            extra_value=self._row_extra_value(row),
            extra_placeholder=self.extra_field_placeholder,
            extra_completions=self._extra_completions() if self.extra_field else None,
            extra_hint=self.extra_field_hint,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        kwargs = {"alias_name": dlg.alias_name}
        if self.extra_field:
            kwargs[self.extra_field] = dlg.extra_value or None
        success = self.controller.update.update_entity(
            self.alias_model_name, alias_id, **kwargs
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

    def _swap_alias(self, row: int):
        if not self.on_swap:
            return
        alias_id = self._row_alias_id(row)
        new_primary = self.table.item(row, 0).text()
        extra_value = self._row_extra_value(row)
        if self.on_swap(alias_id, new_primary, extra_value):
            self._reload_table()
