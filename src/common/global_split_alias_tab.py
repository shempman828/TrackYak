"""
global_split_alias_tab.py

Generic, cross-entity split-alias management table for the unified
"Manage Aliases…" dialog (see docs/specs/split_and_merge_aliases.md).
Lists every rule in a `<Model>SplitAlias` table (grouped by alias_name,
since one alias_name maps to 2+ target rows), with add/edit/delete.
Reusable across Genre/Artist/Publisher/Role -- the split-alias table shape
is identical for each. Splitting an existing entity via SplitDBDialog also
writes rows here automatically (SplitDB._record_split_alias); this widget
additionally lets a rule be set up directly, without an existing combined
entity to split.
"""

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

from src.common.entity_completer_edit import (
    build_entity_search_widget,
    find_or_create_by_name,
    get_cached_entities,
)
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


class _SplitAliasEditDialog(QDialog):
    """Popup for editing an existing split-alias rule's target set."""

    def __init__(
        self, controller, model_name, name_field, id_field, alias_name, target_names, parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Split Alias")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_label = QLabel(alias_name)
        form.addRow("Combined Name:", self.name_label)

        self.targets_edit = build_entity_search_widget(
            controller=controller,
            model_name=model_name,
            name_field=name_field,
            id_field=id_field,
            placeholder_text=f"Target {model_name.lower()}s, separated by ;",
        )
        self.targets_edit.setText(";".join(target_names))
        form.addRow("Splits Into:", self.targets_edit)

        layout.addLayout(form)
        hint = QLabel("<small><i>Separate 2+ names with a semicolon.</i></small>")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if len(self.target_names) < 2:
            QMessageBox.warning(self, "Validation", "Enter 2 or more target names, separated by ;")
            return
        self.accept()

    @property
    def target_names(self) -> list[str]:
        return self.targets_edit.split_names()


class GlobalSplitAliasTab(QWidget):
    """Table of every `<model_name>SplitAlias` rule, with add/edit/delete."""

    def __init__(self, controller, model_name: str, name_field: str, id_field: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.model_name = model_name
        self.name_field = name_field
        self.id_field = id_field
        self.alias_model_name = f"{model_name}SplitAlias"
        self._build_ui()
        self.reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(
            QLabel(
                f"A combined name here auto-splits into every listed {self.model_name.lower()} "
                "on import or manual add, instead of creating one combined entity."
            )
        )

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Combined name, e.g. Viola & Violin")
        add_row.addWidget(self.name_edit, 1)

        self.targets_edit = build_entity_search_widget(
            controller=self.controller,
            model_name=self.model_name,
            name_field=self.name_field,
            id_field=self.id_field,
            placeholder_text="Splits into, e.g. Viola;Violin",
        )
        add_row.addWidget(self.targets_edit, 1)

        self.add_btn = QPushButton("+ Add Rule")
        self.add_btn.clicked.connect(self._add_rule)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Combined Name", "Splits Into", ""])
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
            logger.exception("GlobalSplitAliasTab: failed to load %s", self.alias_model_name)
            rows = []

        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(row.alias_name, []).append(row)

        for alias_name in sorted(grouped, key=str.lower):
            members = sorted(grouped[alias_name], key=lambda r: r.sort_order)
            self._append_row(alias_name, members)

    def _member_display(self, member) -> str:
        target = getattr(member, self.model_name.lower(), None)
        return getattr(target, self.name_field, "") if target is not None else "(deleted)"

    def _append_row(self, alias_name: str, members: list):
        r = self.table.rowCount()
        self.table.insertRow(r)

        self.table.setItem(r, 0, QTableWidgetItem(alias_name))

        target_names = [self._member_display(m) for m in members]
        self.table.setItem(r, 1, QTableWidgetItem(", ".join(target_names)))

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(4)
        edit_btn = QPushButton("Edit")
        edit_btn.setFlat(True)
        edit_btn.clicked.connect(
            lambda checked=False, name=alias_name, targets=target_names: self._edit_rule(
                name, targets
            )
        )
        delete_btn = QPushButton("✕")
        delete_btn.setFlat(True)
        delete_btn.setToolTip("Delete this rule")
        delete_btn.clicked.connect(lambda checked=False, name=alias_name: self._delete_rule(name))
        actions_layout.addWidget(edit_btn)
        actions_layout.addWidget(delete_btn)
        actions_layout.addStretch()
        self.table.setCellWidget(r, 2, actions)
        self.table.setRowHeight(r, 32)

    def _resolve_target_ids(self, names: list[str]) -> list[int] | None:
        """Resolve each typed target name to an entity id, creating any
        that don't already exist (same find-or-create convention as
        every other entity-picker field in the app). Falls back to the
        model's alias table first (e.g. "Pig Robbins" -> ArtistAlias ->
        Hargus Robbins) so a target that's really just an alias resolves
        to its canonical entity instead of creating a duplicate. Returns
        None if any name failed to resolve."""
        known_entities = get_cached_entities(self.controller, self.model_name) or []
        ids = []
        for name in names:
            entity = find_or_create_by_name(
                self.controller,
                self.model_name,
                self.name_field,
                name,
                known_entities,
                extra_lookup=lambda name=name: self.controller.get.resolve_entity_or_alias(
                    self.model_name, self.name_field, name
                ),
            )
            if entity is None:
                return None
            ids.append(getattr(entity, self.id_field))
        return ids

    def _add_rule(self):
        alias_name = self.name_edit.text().strip()
        target_names = self.targets_edit.split_names()
        if not alias_name:
            show_status_message(self, "Enter a combined name.")
            return
        if len(target_names) < 2:
            show_status_message(self, "Enter 2 or more target names, separated by ;")
            return

        target_ids = self._resolve_target_ids(target_names)
        if target_ids is None:
            show_status_message(self, "Could not resolve every target name.")
            return

        try:
            ok = self.controller.split.set_split_alias(self.model_name, alias_name, target_ids)
        except SQLAlchemyError as exc:
            logger.exception("GlobalSplitAliasTab: failed to add rule %r", alias_name)
            QMessageBox.critical(self, "Error", f"Could not add rule:\n{exc}")
            return
        if not ok:
            show_status_message(self, "Could not add rule.")
            return

        self.name_edit.clear()
        self.targets_edit.reset()
        self.reload()

    def _edit_rule(self, alias_name: str, current_targets: list[str]):
        dlg = _SplitAliasEditDialog(
            self.controller,
            self.model_name,
            self.name_field,
            self.id_field,
            alias_name,
            current_targets,
            parent=self,
        )
        if dlg.exec_() != QDialog.Accepted:
            return

        target_ids = self._resolve_target_ids(dlg.target_names)
        if target_ids is None:
            show_status_message(self, "Could not resolve every target name.")
            return

        try:
            self.controller.split.set_split_alias(self.model_name, alias_name, target_ids)
        except SQLAlchemyError as exc:
            logger.exception("GlobalSplitAliasTab: failed to update rule %r", alias_name)
            QMessageBox.critical(self, "Error", f"Could not update rule:\n{exc}")
            return
        self.reload()

    def _delete_rule(self, alias_name: str):
        reply = QMessageBox.question(
            self,
            "Delete Split Alias",
            f"Delete the split rule for <b>{alias_name}</b>?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.controller.split.delete_split_alias(self.model_name, alias_name)
        except SQLAlchemyError as exc:
            logger.exception("GlobalSplitAliasTab: failed to delete rule %r", alias_name)
            QMessageBox.critical(self, "Error", f"Could not delete rule:\n{exc}")
            return
        self.reload()
