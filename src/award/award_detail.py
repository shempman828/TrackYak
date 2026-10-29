"""Editable detail panel for one award."""

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget

from src.award.award_common import ENTITY_FIELDS, RECIPIENT, award_display_text, delete_awards, entity_display_name, parse_award_year
from src.award.award_relationship_dialog import AwardRelationshipDialog
from src.common.hierarchy_ops import hierarchy_descendant_ids
from src.core.status_widget import show_status_message
from src.foundation.logger_config import logger


class AwardDetailTab(QWidget):
    """Detail panel that edits one award and its relationships."""

    saved = Signal()
    deleted = Signal()

    def __init__(self, award: Any, controller: Any):
        super().__init__()
        self.award = award
        self.controller = controller
        self._original_values: dict = {}
        self.init_ui()
        self._original_values = self._form_values()
        self._update_save_state()

    def init_ui(self) -> None:
        """Set up the scroll area and the sections."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)

        self._create_basic_info_section()
        self._populate_parent_combo()
        self._create_relationships_section()
        self._create_description_section()
        self._create_actions_section()
        self._refresh_relationships()

        scroll.setWidget(content)
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

    def _create_basic_info_section(self) -> None:
        """Create the name, year, category, link and parent fields."""
        basic_group = QGroupBox("Basic Information")
        layout = QGridLayout()

        def add_row(row: int, label: str, widget: QWidget) -> None:
            label_widget = QLabel(label)
            label_widget.setBuddy(widget)
            layout.addWidget(label_widget, row, 0)
            layout.addWidget(widget, row, 1)

        self.name_edit = QLineEdit(self.award.award_name or "")
        self.name_edit.textChanged.connect(self._update_save_state)
        add_row(0, "Award &Name:", self.name_edit)

        self.year_edit = QLineEdit(str(self.award.award_year) if self.award.award_year else "")
        self.year_edit.setPlaceholderText("e.g., 2024")
        self.year_edit.textChanged.connect(self._update_save_state)
        add_row(1, "&Year:", self.year_edit)

        self.category_edit = QLineEdit(self.award.award_category or "")
        self.category_edit.setPlaceholderText("e.g., Best Album, Lifetime Achievement")
        self.category_edit.textChanged.connect(self._update_save_state)
        add_row(2, "&Category:", self.category_edit)

        self.wikipedia_edit = QLineEdit(self.award.wikipedia_link or "")
        self.wikipedia_edit.setPlaceholderText("https://en.wikipedia.org/...")
        self.wikipedia_edit.textChanged.connect(self._update_save_state)
        add_row(3, "&Wikipedia Link:", self.wikipedia_edit)

        self.parent_combo = QComboBox()
        self.parent_combo.currentIndexChanged.connect(self._update_save_state)
        add_row(4, "&Parent Award:", self.parent_combo)

        basic_group.setLayout(layout)
        self._content_layout.addWidget(basic_group)

    def _populate_parent_combo(self) -> None:
        """Fill the parent combo, excluding this award and its descendants (they would form a cycle)."""
        self.parent_combo.blockSignals(True)
        try:
            self.parent_combo.addItem("None", None)
            awards = self.controller.get.get_all_entities("Award")
            excluded = {self.award.award_id} | hierarchy_descendant_ids([self.award.award_id], awards, id_attr="award_id")
            candidates = [a for a in awards if a.award_id not in excluded]
            for award in sorted(candidates, key=lambda a: award_display_text(a).casefold()):
                self.parent_combo.addItem(award_display_text(award), award.award_id)
            index = self.parent_combo.findData(self.award.parent_id) if self.award.parent_id else 0
            self.parent_combo.setCurrentIndex(max(index, 0))
        finally:
            self.parent_combo.blockSignals(False)

    def _create_relationships_section(self) -> None:
        """Create the relationship list and its Add/Remove buttons."""
        rel_group = QGroupBox("Award Relationships")
        layout = QVBoxLayout()

        self.recipient_label = QLabel("No recipient")
        self.recipient_label.setWordWrap(True)
        layout.addWidget(self.recipient_label)

        self.relationship_list = QListWidget()
        self.relationship_list.setMinimumHeight(100)
        self.relationship_list.itemSelectionChanged.connect(self._on_relationship_selection_changed)
        layout.addWidget(self.relationship_list)

        button_row = QHBoxLayout()
        self.set_recipient_btn = QPushButton("Add Recipient...")
        self.set_recipient_btn.clicked.connect(lambda: self._open_relationship_dialog(RECIPIENT))
        button_row.addWidget(self.set_recipient_btn)

        self.manage_relationships_btn = QPushButton("Add Relationship...")
        self.manage_relationships_btn.clicked.connect(lambda: self._open_relationship_dialog("nominee"))
        button_row.addWidget(self.manage_relationships_btn)

        self.remove_relationship_btn = QPushButton("Remove")
        self.remove_relationship_btn.setEnabled(False)
        self.remove_relationship_btn.clicked.connect(self._remove_selected_relationship)
        button_row.addWidget(self.remove_relationship_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        rel_group.setLayout(layout)
        self._content_layout.addWidget(rel_group)

    def _create_description_section(self) -> None:
        """Create the description editor."""
        desc_group = QGroupBox("Description")
        layout = QVBoxLayout()

        self.description_edit = QTextEdit()
        self.description_edit.setPlainText(self.award.award_description or "")
        self.description_edit.setPlaceholderText("Enter award description, ceremony details, or notes...")
        self.description_edit.textChanged.connect(self._update_save_state)
        self.description_edit.setMinimumHeight(120)
        layout.addWidget(self.description_edit)

        desc_group.setLayout(layout)
        self._content_layout.addWidget(desc_group)

    def _create_actions_section(self) -> None:
        """Create the Save and Delete buttons."""
        action_group = QGroupBox("Actions")
        layout = QHBoxLayout()

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_changes)
        layout.addWidget(self.save_btn)

        self.delete_btn = QPushButton("Delete Award")
        self.delete_btn.clicked.connect(self._delete_award)
        layout.addWidget(self.delete_btn)

        action_group.setLayout(layout)
        self._content_layout.addWidget(action_group)

    # ─────────────────────────────────────────────── Relationships

    def _open_relationship_dialog(self, default_relationship: str) -> None:
        """Open the relationship picker with `default_relationship` preselected."""
        dialog = AwardRelationshipDialog(self.award, self.controller, self, default_relationship=default_relationship)
        dialog.relationship_added.connect(self._on_relationship_added)
        dialog.exec()

    def _refresh_relationships(self) -> None:
        """Reload the relationship list and the recipient summary."""
        associations = self.controller.get.get_all_entities("AwardAssociation", award_id=self.award.award_id)

        # One query per entity type rather than one per association.
        ids_by_type: dict[str, list[int]] = defaultdict(list)
        for assoc in associations:
            if assoc.entity_type in ENTITY_FIELDS:
                ids_by_type[assoc.entity_type].append(assoc.entity_id)
        entities: dict[tuple[str, int], Any] = {}
        for entity_type, ids in ids_by_type.items():
            id_attr = ENTITY_FIELDS[entity_type][1]
            for entity in self.controller.get.get_all_entities(entity_type, **{f"{id_attr}__in": ids}):
                entities[(entity_type, getattr(entity, id_attr))] = entity

        rows = []
        for assoc in associations:
            entity = entities.get((assoc.entity_type, assoc.entity_id))
            name = entity_display_name(assoc.entity_type, entity) if entity else "Not found"
            rows.append((assoc, f"{assoc.entity_type}: {name}"))
        # Recipients first, then by relationship type and name.
        rows.sort(key=lambda r: (r[0].association_type != RECIPIENT, r[0].association_type or "", r[1].casefold()))

        self.relationship_list.clear()
        for assoc, label in rows:
            item = QListWidgetItem(f"{(assoc.association_type or 'related').capitalize()} - {label}")
            item.setData(Qt.UserRole, assoc.association_id)
            self.relationship_list.addItem(item)

        recipients = [label for assoc, label in rows if assoc.association_type == RECIPIENT]
        self.recipient_label.setText(f"Recipient: {', '.join(recipients)}" if recipients else "No recipient")
        self._on_relationship_selection_changed()

    def _on_relationship_selection_changed(self) -> None:
        """Enable Remove only while a relationship is selected."""
        self.remove_relationship_btn.setEnabled(bool(self.relationship_list.selectedItems()))

    def _on_relationship_added(self, entity_type: str, entity_id: int, relationship_type: str) -> None:
        """Store a relationship picked in the dialog, skipping exact duplicates."""
        key = {"award_id": self.award.award_id, "entity_type": entity_type, "entity_id": entity_id, "association_type": relationship_type}
        if self.controller.get.get_all_entities("AwardAssociation", **key):
            show_status_message(self, "That relationship already exists.")
            return
        if not self.controller.add.add_entity("AwardAssociation", **key):
            QMessageBox.critical(self, "Error", "Failed to add the relationship. See the log for details.")
            return
        self._refresh_relationships()
        logger.info(f"Added {relationship_type} relationship: {entity_type} {entity_id} to award {self.award.award_id}")

    def _remove_selected_relationship(self) -> None:
        """Delete the selected relationship row."""
        items = self.relationship_list.selectedItems()
        if not items:
            return
        association_id = items[0].data(Qt.UserRole)
        if not self.controller.delete.delete_entity("AwardAssociation", association_id):
            QMessageBox.critical(self, "Error", "Failed to remove the relationship. See the log for details.")
            return
        self._refresh_relationships()
        show_status_message(self, "Relationship removed")

    # ─────────────────────────────────────────────── Save / delete

    def _form_values(self) -> dict:
        """Current raw field values, for change detection."""
        return {
            "name": self.name_edit.text(),
            "year": self.year_edit.text(),
            "category": self.category_edit.text(),
            "wikipedia": self.wikipedia_edit.text(),
            "description": self.description_edit.toPlainText(),
            "parent_id": self.parent_combo.currentData(),
        }

    def is_dirty(self) -> bool:
        """True when a field differs from the last saved state."""
        return self._form_values() != self._original_values

    def _update_save_state(self) -> None:
        """Enable Save only when there are unsaved changes."""
        self.save_btn.setEnabled(self.is_dirty())

    def _save_changes(self) -> None:
        """Validate and save the fields, then emit `saved`."""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Award name is required.")
            return
        try:
            award_year = parse_award_year(self.year_edit.text())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Year", str(e))
            return

        updates = {
            "award_name": name,
            "award_year": award_year,
            "award_category": self.category_edit.text().strip() or None,
            "award_description": self.description_edit.toPlainText().strip() or None,
            "wikipedia_link": self.wikipedia_edit.text().strip() or None,
            "parent_id": self.parent_combo.currentData(),
        }
        if not self.controller.update.update_entity("Award", self.award.award_id, **updates):
            QMessageBox.critical(self, "Error", "Failed to save changes. See the log for details.")
            return

        self._original_values = self._form_values()
        self._update_save_state()
        logger.info(f"Updated award {self.award.award_id}")
        show_status_message(self, "Award saved")
        self.saved.emit()

    def _delete_award(self) -> None:
        """Delete this award after confirmation, then emit `deleted`."""
        children = self.controller.get.get_all_entities("Award", parent_id=self.award.award_id)
        message = f"Are you sure you want to delete the award '{self.award.award_name}'?"
        if children:
            message += f"\n\nIt has {len(children)} child award(s). They will not be deleted -- they will become top-level awards."

        reply = QMessageBox.question(self, "Confirm Delete", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        if not delete_awards(self.controller, [self.award.award_id]):
            QMessageBox.critical(self, "Error", "Failed to delete the award. See the log for details.")
            return
        logger.info(f"Deleted award {self.award.award_id}")
        show_status_message(self, "Award deleted")
        self.deleted.emit()
