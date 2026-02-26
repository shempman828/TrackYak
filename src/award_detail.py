from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from src.award_relationship_dialog import AwardRelationshipDialog
from src.logger_config import logger
from src.wikipedia_seach import search_wikipedia


class AwardDetailTab(QWidget):
    """Detailed award information tab with editing functionality."""

    save_requested = Signal()

    def __init__(self, award: Any, controller: Any):
        super().__init__()
        self.award = award
        self.controller = controller
        self._original_values = {}
        self.init_ui()

    def init_ui(self) -> None:
        """Set up the tab layout with scroll area and sections."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.layout = QVBoxLayout(content)

        self._create_basic_info_section()
        self._create_relationships_section()
        self._create_description_section()
        self._create_actions_section()

        # Populate current associations after UI is built
        self._refresh_recipient_display()

        scroll.setWidget(content)
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

    def _create_basic_info_section(self) -> None:
        """Create section for basic award information."""
        basic_group = QGroupBox("Basic Information")
        layout = QGridLayout()

        # Award Name
        layout.addWidget(QLabel("Award Name:"), 0, 0)
        self.name_edit = QLineEdit(self.award.award_name or "")
        self.name_edit.textChanged.connect(self._enable_save)
        layout.addWidget(self.name_edit, 0, 1)

        # Award Year
        layout.addWidget(QLabel("Year:"), 1, 0)
        self.year_edit = QLineEdit(
            str(self.award.award_year) if self.award.award_year else ""
        )
        self.year_edit.setPlaceholderText("e.g., 2024")
        self.year_edit.textChanged.connect(self._enable_save)
        layout.addWidget(self.year_edit, 1, 1)

        # Award Category
        layout.addWidget(QLabel("Category:"), 2, 0)
        self.category_edit = QLineEdit(self.award.award_category or "")
        self.category_edit.setPlaceholderText("e.g., Best Album, Lifetime Achievement")
        self.category_edit.textChanged.connect(self._enable_save)
        layout.addWidget(self.category_edit, 2, 1)

        basic_group.setLayout(layout)
        self.layout.addWidget(basic_group)

    def _create_relationships_section(self) -> None:
        """Create section for award relationships."""
        rel_group = QGroupBox("Award Relationships")
        layout = QVBoxLayout()

        # Recipient section
        recipient_layout = QHBoxLayout()
        recipient_layout.addWidget(QLabel("Recipient:"))
        self.recipient_label = QLabel("No recipient")
        self.recipient_label.setWordWrap(True)
        recipient_layout.addWidget(self.recipient_label, 1)

        self.set_recipient_btn = QPushButton("Set Recipient")
        self.set_recipient_btn.clicked.connect(self._open_relationship_dialog)
        recipient_layout.addWidget(self.set_recipient_btn)

        self.clear_recipient_btn = QPushButton("Clear")
        self.clear_recipient_btn.clicked.connect(self._clear_recipient)
        self.clear_recipient_btn.setEnabled(False)
        recipient_layout.addWidget(self.clear_recipient_btn)

        layout.addLayout(recipient_layout)

        # Other relationships section
        layout.addWidget(QLabel("Other Relationships:"))
        self.other_relationships_label = QLabel("No other relationships")
        self.other_relationships_label.setWordWrap(True)
        layout.addWidget(self.other_relationships_label)

        # Manage all relationships button
        self.manage_relationships_btn = QPushButton("Manage All Relationships")
        self.manage_relationships_btn.clicked.connect(self._open_relationship_dialog)
        layout.addWidget(self.manage_relationships_btn)

        rel_group.setLayout(layout)
        self.layout.addWidget(rel_group)

    def _relationships_key_press_event(self, event):
        """Handle key press events for the relationships widget."""
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._remove_selected_relationship()
        else:
            # Call the original keyPressEvent for other keys
            QTreeWidget.keyPressEvent(self.relationships_widget, event)

    def _remove_selected_relationship(self):
        """Remove the currently selected relationship."""
        selected_items = self.relationships_widget.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        # Get the association ID from the item data
        association_id = item.data(0, Qt.UserRole)

        if association_id:
            self._remove_relationship(association_id)

    def _open_relationship_dialog(self) -> None:
        """Open the relationship management dialog."""
        dialog = AwardRelationshipDialog(self.award, self.controller, self)
        dialog.relationship_added.connect(self._on_relationship_added)
        dialog.exec()

    def _refresh_recipient_display(self) -> None:
        try:
            associations = self.controller.get.get_all_entities(
                "AwardAssociation", award_id=self.award.award_id
            )
        except Exception as e:
            logger.error(f"Error refreshing relationships: {e}")
            self.recipient_label.setText("Error loading")
            self.other_relationships_label.setText("Error loading")
            self.clear_recipient_btn.setEnabled(False)
            return

        lookup = {
            "Artist": ("Artist", "artist_id"),
            "Album": ("Album", "album_id"),
            "Track": ("Track", "track_id"),
            "Publisher": ("Publisher", "publisher_id"),
            "Place": ("Place", "place_id"),
        }

        def name_of(entity):
            for attr in (
                "name",
                "artist_name",
                "album_name",
                "track_name",
                "publisher_name",
                "place_name",
            ):
                if hasattr(entity, attr):
                    return getattr(entity, attr)
            return "Unknown"

        # Separate recipient from other relationships
        recipient_text = "No recipient"
        other_relationships = []
        has_recipient = False

        for assoc in associations:
            model = lookup.get(assoc.entity_type)
            if not model:
                continue

            model_name, id_field = model
            try:
                entity = self.controller.get.get_entity_object(
                    model_name, **{id_field: assoc.entity_id}
                )
                display_name = name_of(entity) if entity else "Not found"

                relationship_text = f"{assoc.entity_type}: {display_name}"
                if assoc.association_type:
                    relationship_text = (
                        f"{assoc.association_type} - {relationship_text}"
                    )

                # Check if this is the recipient
                if assoc.association_type == "recipient":
                    recipient_text = relationship_text
                    has_recipient = True
                else:
                    other_relationships.append(relationship_text)

            except Exception as e:
                logger.error(
                    f"Error loading entity {assoc.entity_type} {assoc.entity_id}: {e}"
                )
                error_text = f"{assoc.entity_type}: Error loading"
                if assoc.association_type == "recipient":
                    recipient_text = error_text
                    has_recipient = True
                else:
                    other_relationships.append(error_text)

        # Update recipient display
        self.recipient_label.setText(recipient_text)
        self.clear_recipient_btn.setEnabled(has_recipient)

        # Update other relationships display
        if other_relationships:
            self.other_relationships_label.setText("\n".join(other_relationships))
        else:
            self.other_relationships_label.setText("No other relationships")

    def _clear_recipient(self) -> None:
        """Clear only the recipient relationship."""
        try:
            associations = self.controller.get.get_all_entities(
                "AwardAssociation", award_id=self.award.award_id
            )

            # Remove only recipient associations
            for assoc in associations:
                if assoc.association_type == "recipient":
                    self.controller.delete.delete_entity(
                        "AwardAssociation", assoc.association_id
                    )

            self._enable_save()
            self._refresh_recipient_display()
            logger.info(f"Cleared recipient for award {self.award.award_id}")

        except Exception as e:
            logger.error(f"Error clearing recipient: {e}")
            QMessageBox.critical(self, "Error", f"Failed to clear recipient: {e}")

    def _create_description_section(self) -> None:
        """Create section for award description."""
        desc_group = QGroupBox("Description")
        layout = QVBoxLayout()

        self.description_edit = QTextEdit()
        self.description_edit.setPlainText(self.award.award_description or "")
        self.description_edit.setPlaceholderText(
            "Enter award description, ceremony details, or notes..."
        )
        self.description_edit.textChanged.connect(self._enable_save)
        self.description_edit.setMinimumHeight(120)
        layout.addWidget(self.description_edit)

        self.wikipedia_search_btn = QPushButton("Search Wikipedia for Description")
        self.wikipedia_search_btn.clicked.connect(self._search_wikipedia)
        layout.addWidget(self.wikipedia_search_btn)

        desc_group.setLayout(layout)
        self.layout.addWidget(desc_group)

    def _create_actions_section(self) -> None:
        """Create action buttons section."""
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
        self.layout.addWidget(action_group)

    def _search_wikipedia(self) -> None:
        """Search Wikipedia for award description."""
        try:
            # Get all return values from the search
            title, summary, full_content, link, images = search_wikipedia(
                self.award.award_name, parent=self
            )

            if summary:
                # Update the description field with the summary
                self.description_edit.setPlainText(full_content)
                self._enable_save()  # Enable save since we modified content

                # Show success message
                QMessageBox.information(
                    self,
                    "Wikipedia Search Complete",
                    f"Found Wikipedia article: {title}\n\nDescription has been populated. Click 'Save Changes' to persist it to the database.",
                )
            else:
                QMessageBox.information(
                    self,
                    "No Results Found",
                    "No Wikipedia article found for this award name. You can manually enter the description.",
                )

        except Exception as e:
            logger.error(f"Error searching Wikipedia: {e}")
            QMessageBox.critical(self, "Error", f"Failed to search Wikipedia: {e}")

    def _populate_parent_combo(self) -> None:
        """Populate parent award combo box, excluding current award."""
        self.parent_combo.addItem("None", None)
        try:
            awards = self.controller.get.get_all_entities("Award")
            for award in awards:
                if award.award_id != self.award.award_id:  # Exclude self
                    display_text = award.award_name
                    if award.award_year:
                        display_text = f"[{award.award_year}] {display_text}"
                    self.parent_combo.addItem(display_text, award.award_id)

            # Set current parent
            if self.award.parent_id:
                for i in range(self.parent_combo.count()):
                    if self.parent_combo.itemData(i) == self.award.parent_id:
                        self.parent_combo.setCurrentIndex(i)
                        break
        except Exception as e:
            logger.error(f"Error populating parent awards: {e}")

    def _enable_save(self) -> None:
        """Enable the save button when changes are detected."""
        self.save_btn.setEnabled(True)

    def _save_changes(self) -> None:
        """Save changes to the award and refresh parent view."""
        try:
            # Validate year
            year_text = self.year_edit.text().strip()
            award_year = int(year_text) if year_text else None

            if award_year and (award_year < 1000 or award_year > 2999):
                QMessageBox.warning(
                    self,
                    "Invalid Year",
                    "Please enter a valid year between 1000 and 2999.",
                )
                return

            # First update the basic award information
            updates = {
                "award_name": self.name_edit.text().strip(),
                "award_year": award_year,
                "award_category": self.category_edit.text().strip() or None,
                "award_description": self.description_edit.toPlainText().strip()
                or None,
                "parent_id": self.parent_combo.currentData(),
            }

            self.controller.update.update_entity(
                "Award", self.award.award_id, **updates
            )

            self.save_btn.setEnabled(False)

            # Update the tab name
            tab_widget = None
            parent = self.parent()
            while parent and not isinstance(parent, QTabWidget):
                parent = parent.parent()

            if isinstance(parent, QTabWidget):
                tab_widget = parent
                tab_index = tab_widget.indexOf(self)
                if tab_index != -1:
                    new_name = self.name_edit.text().strip()
                    tab_name = f"{new_name[:15]}..." if len(new_name) > 15 else new_name
                    tab_widget.setTabText(tab_index, tab_name)

            # Emit signal to refresh the award list and tree
            if hasattr(self.parent().parent(), "award_updated"):
                self.parent().parent().award_updated.emit()

            logger.info(f"Updated award {self.award.award_id}")

        except ValueError:
            QMessageBox.warning(
                self, "Invalid Year", "Please enter a valid numeric year."
            )
        except Exception as e:
            logger.error(f"Error saving award: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save changes: {str(e)}")

    def _delete_award(self) -> None:
        """Delete the current award after confirmation."""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the award '{self.award.award_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                # Delete the award - associations will be automatically deleted by ORM cascade
                self.controller.delete.delete_entity("Award", self.award.award_id)
                QMessageBox.information(self, "Deleted", "Award deleted successfully")
                logger.info(f"Deleted award {self.award.award_id}")

                # Emit signal to notify parent to close this tab
                self.save_requested.emit()

            except Exception as e:
                logger.error(f"Error deleting award: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete award: {str(e)}")

    def _on_relationship_added(
        self, entity_type: str, entity_id: int, relationship_type: str
    ) -> None:
        """Handle new relationship from dialog."""
        try:
            # Create the award association
            association_data = {
                "award_id": self.award.award_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "association_type": relationship_type,
            }

            self.controller.add.add_entity("AwardAssociation", **association_data)

            self._enable_save()
            self._refresh_recipient_display()
            logger.info(
                f"Added {relationship_type} relationship: {entity_type} {entity_id} to award {self.award.award_id}"
            )

        except Exception as e:
            logger.error(f"Error adding relationship: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add relationship: {e}")
