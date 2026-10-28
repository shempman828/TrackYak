"""Alias table embeddable in an entity's edit dialog, for any `<Model>Alias` model."""

# Optional hooks: extra_field (second free-text column with autocomplete; see
# _extra_completions), on_swap(alias_id, new_name, extra) -> bool ("Use as
# Name"), and on_before_add(name) -> bool (False aborts the add).

from collections.abc import Callable
import html

from PySide6.QtCore import QStringListModel, Qt
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
from sqlalchemy.exc import SQLAlchemyError

from src.core.status_widget import show_status_message
from src.foundation.logger_config import logger


class EntityAliasEditDialog(QDialog):
    """Dialog for editing an existing alias's name and optional extra field (e.g. a "type")."""

    def __init__(
        self,
        alias_name: str = "",
        placeholder: str = "",
        extra_field_label: str | None = None,
        extra_value: str = "",
        extra_placeholder: str = "",
        extra_completions: list[str] | None = None,
        extra_hint: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Alias")
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
        """The edited alias name, trimmed."""
        return self.name_edit.text().strip()

    @property
    def extra_value(self) -> str:
        """The edited extra-field value, trimmed ("" when there is no extra field)."""
        return self.extra_edit.text().strip() if self.extra_edit else ""


class EntityAliasRowWidget(QWidget):
    """Compact Edit / [Use as Name] / ✕ buttons for one alias row; swap appears only with swap_cb."""

    def __init__(self, edit_cb, delete_cb, swap_cb=None, swap_label: str = "↕ Use as Name", swap_tooltip: str = "Promote this alias to the entity's primary name", parent=None):
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
        extra_field: str | None = None,
        extra_field_label: str = "Type",
        extra_field_placeholder: str = "",
        extra_field_hint: str = "",
        on_swap: Callable[[int, str, str], bool] | None = None,
        on_before_add: Callable[[str], bool] | None = None,
        list_style: bool = False,
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
        # A bare list of rows (name + actions) instead of a QTableWidget --
        # used where a full table with headers is overkill, e.g. publisher
        # aliases which have no extra field. See publisher_edit_dialog.py.
        self.list_style = list_style
        self._list_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)

        self.name_edit = QLineEdit()
        if self.placeholder:
            self.name_edit.setPlaceholderText(self.placeholder)
        self.name_edit.textChanged.connect(self._on_add_name_changed)
        self.name_edit.returnPressed.connect(self._add_alias)
        add_row.addWidget(self.name_edit, 2)

        self.extra_edit = None
        self._extra_completer = None
        if self.extra_field:
            self.extra_edit = QLineEdit()
            if self.extra_field_placeholder:
                self.extra_edit.setPlaceholderText(self.extra_field_placeholder)
            self.extra_edit.setClearButtonEnabled(True)
            self.extra_edit.returnPressed.connect(self._add_alias)
            self._extra_completer = QCompleter(self)
            self._extra_completer.setCaseSensitivity(Qt.CaseInsensitive)
            self._extra_completer.setFilterMode(Qt.MatchContains)
            self._extra_completer.setCompletionMode(QCompleter.PopupCompletion)
            self.extra_edit.setCompleter(self._extra_completer)
            add_row.addWidget(self.extra_edit, 1)

        self.add_btn = QPushButton("Add Alias")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._add_alias)
        add_row.addWidget(self.add_btn)

        layout.addLayout(add_row)

        if self.extra_field and self.extra_field_hint:
            hint = QLabel(f"<small><i>{self.extra_field_hint}</i></small>")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        if self.list_style:
            self.table = None
            self.list_container = QWidget()
            self.list_layout = QVBoxLayout(self.list_container)
            self.list_layout.setContentsMargins(0, 0, 0, 0)
            self.list_layout.setSpacing(2)
            layout.addWidget(self.list_container)
            # Without this, list_container is the sole remaining item in a
            # QVBoxLayout with no expanding-policy widgets, so Qt stretches
            # it (and in turn its one row) to fill all leftover vertical
            # space instead of leaving rows packed at the top.
            layout.addStretch()
            return

        headers = ["Alias Name"]
        if self.extra_field:
            headers.append(self.extra_field_label)
        headers.append("")
        actions_col = len(headers) - 1

        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        if self.extra_field:
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(actions_col, QHeaderView.Fixed)
        self.table.setColumnWidth(actions_col, 180 if self.on_swap else 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        layout.addWidget(self.table)

    def _on_add_name_changed(self, text: str):
        self.add_btn.setEnabled(bool(text.strip()))

    def _refresh_extra_completer(self):
        if not self._extra_completer:
            return
        self._extra_completer.setModel(QStringListModel(self._extra_completions(), self._extra_completer))

    def load(self, entity):
        """Show `entity`'s aliases."""
        self.entity = entity
        self._reload_table()

    def _entity_id(self):
        return getattr(self.entity, self.id_field)

    def _clear_rows(self):
        if not self.list_style:
            self.table.setRowCount(0)
            return
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # setParent(None) detaches it immediately so it stops
                # painting at its old position right away -- deleteLater()
                # alone leaves it a visible, stale child until the event
                # loop gets around to the deferred delete.
                widget.setParent(None)
                widget.deleteLater()
        self._list_rows = []

    def _row_count(self) -> int:
        return len(self._list_rows) if self.list_style else self.table.rowCount()

    def _reload_table(self):
        self._clear_rows()
        try:
            aliases = self.controller.get.get_all_entities(self.alias_model_name, **{self.id_field: self._entity_id()})
        except (AttributeError, SQLAlchemyError):
            logger.exception("EntityAliasesTab: failed to fetch %s for %s", self.alias_model_name, self.id_field)
            aliases = []

        for alias in aliases:
            extra_value = (getattr(alias, self.extra_field, "") or "") if self.extra_field else None
            self._append_row(alias.alias_id, alias.alias_name, extra_value)

        if self.list_style and not aliases:
            empty = QLabel("No aliases yet.")
            empty.setProperty("textRole", "muted")
            self.list_layout.addWidget(empty)

        self._refresh_extra_completer()

    def _append_row(self, alias_id: int, alias_name: str, extra_value: str | None = None):
        if self.list_style:
            self._append_list_row(alias_id, alias_name, extra_value)
            return
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

    def _append_list_row(self, alias_id: int, alias_name: str, extra_value: str | None = None):
        row_widget = QWidget()
        row_widget.setProperty("aliasId", alias_id)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(2, 2, 2, 2)
        row_layout.setSpacing(8)

        name_label = QLabel(alias_name)
        row_layout.addWidget(name_label, 1)

        extra_label = None
        if self.extra_field:
            extra_label = QLabel(extra_value or "")
            extra_label.setProperty("textRole", "muted")
            row_layout.addWidget(extra_label)

        row = len(self._list_rows)
        actions = EntityAliasRowWidget(
            edit_cb=lambda checked=False, r=row: self._edit_alias(r),
            delete_cb=lambda checked=False, r=row: self._delete_alias(r),
            swap_cb=(lambda checked=False, r=row: self._swap_alias(r)) if self.on_swap else None,
        )
        row_layout.addWidget(actions)

        self.list_layout.addWidget(row_widget)
        self._list_rows.append((row_widget, name_label, extra_label))

    def _row_alias_id(self, row: int) -> int:
        if self.list_style:
            return self._list_rows[row][0].property("aliasId")
        return self.table.item(row, 0).data(Qt.UserRole)

    def _row_alias_name(self, row: int) -> str:
        if self.list_style:
            return self._list_rows[row][1].text()
        return self.table.item(row, 0).text()

    def _row_extra_value(self, row: int) -> str:
        if not self.extra_field:
            return ""
        if self.list_style:
            extra_label = self._list_rows[row][2]
            return extra_label.text().strip() if extra_label else ""
        item = self.table.item(row, 1)
        return item.text().strip() if item else ""

    def _existing_extra_values(self) -> list[str]:
        """Distinct extra-field values already in the table, for autocomplete."""
        if not self.extra_field:
            return []
        values = {self._row_extra_value(r) for r in range(self._row_count())}
        values.discard("")
        return sorted(values)

    def _extra_completions(self) -> list[str]:
        """Extra-field suggestions for the add row and edit dialog; override to add fixed values."""
        return self._existing_extra_values()

    def _add_alias(self):
        alias_name = self.name_edit.text().strip()
        if not alias_name:
            return
        extra_value = self.extra_edit.text().strip() if self.extra_edit else ""
        if self.on_before_add and not self.on_before_add(alias_name):
            self._reload_table()
            return
        kwargs = {self.id_field: self._entity_id(), "alias_name": alias_name}
        if self.extra_field:
            kwargs[self.extra_field] = extra_value or None
        # add/update/delete_entity log and return None/False on failure; they do not raise.
        if self.controller.add.add_entity(self.alias_model_name, **kwargs) is None:
            QMessageBox.critical(self, "Error", f"Could not add alias '{alias_name}'. The name may already be in use.")
            return
        self.name_edit.clear()
        if self.extra_edit:
            self.extra_edit.clear()
        self._reload_table()

    def _edit_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self._row_alias_name(row)
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
        success = self.controller.update.update_entity(self.alias_model_name, alias_id, **kwargs)
        if not success:
            show_status_message(self, f"Could not rename alias to '{dlg.alias_name}'. That name may already be in use.")
            return
        self._reload_table()

    def _delete_alias(self, row: int):
        alias_id = self._row_alias_id(row)
        alias_name = self._row_alias_name(row)
        reply = QMessageBox.question(self, "Delete Alias", f"Delete alias <b>{html.escape(alias_name)}</b>?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if not self.controller.delete.delete_entity(self.alias_model_name, alias_id):
            QMessageBox.critical(self, "Error", f"Could not delete alias '{alias_name}'.")
            return
        self._reload_table()

    def _swap_alias(self, row: int):
        if not self.on_swap:
            return
        alias_id = self._row_alias_id(row)
        new_primary = self._row_alias_name(row)
        extra_value = self._row_extra_value(row)
        if self.on_swap(alias_id, new_primary, extra_value):
            self._reload_table()
