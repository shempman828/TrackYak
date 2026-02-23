"""
Dialog for adding a new award to an entity (Artist, Album, Track, Publisher).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from logger_config import logger


class AddAwardDialog(QDialog):
    """Dialog for creating a new award and associating it with an entity."""

    # Signal emitted when award is successfully added
    award_added = Signal(int)  # award_id

    def __init__(self, controller, entity_type, entity_id, parent=None):
        """
        Initialize the dialog.

        Args:
            controller: Main controller instance
            entity_type: Type of entity ('Artist', 'Album', 'Track', 'Publisher')
            entity_id: ID of the entity to award
            parent: Parent widget
        """
        super().__init__(parent)
        self.controller = controller
        self.entity_type = entity_type
        self.entity_id = entity_id

        self.setWindowTitle(f"Add Award to {entity_type}")
        self.setMinimumWidth(500)

        self.init_ui()
        self.setup_connections()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            f"Creating new award for {self.entity_type} ID: {self.entity_id}"
        )
        layout.addWidget(info_label)

        # Form layout for award details
        form_layout = QFormLayout()

        # Award Name (required)
        self.award_name_edit = QLineEdit()
        self.award_name_edit.setPlaceholderText(
            "e.g., Grammy Award, Billboard Music Award"
        )
        form_layout.addRow("Award Name*:", self.award_name_edit)

        # Award Year
        self.award_year_spin = QSpinBox()
        self.award_year_spin.setRange(1900, 2100)
        self.award_year_spin.setValue(2024)
        self.award_year_spin.setSpecialValueText("Unknown")
        form_layout.addRow("Award Year:", self.award_year_spin)

        # Award Category
        self.award_category_edit = QLineEdit()
        self.award_category_edit.setPlaceholderText(
            "e.g., Best New Artist, Album of the Year"
        )
        form_layout.addRow("Category:", self.award_category_edit)

        # Association Type
        self.association_type_combo = QComboBox()
        self.association_type_combo.addItems(
            [
                "recipient",
                "winner",
                "nominee",
                "honoree",
                "finalist",
                "awarded",
                "presented",
            ]
        )
        self.association_type_combo.setCurrentText("recipient")
        form_layout.addRow("Association Type:", self.association_type_combo)

        # Award Description
        self.award_description_edit = QTextEdit()
        self.award_description_edit.setMaximumHeight(100)
        self.award_description_edit.setPlaceholderText(
            "Optional description of the award..."
        )
        form_layout.addRow("Description:", self.award_description_edit)

        # Wikipedia Link
        self.wikipedia_link_edit = QLineEdit()
        self.wikipedia_link_edit.setPlaceholderText("https://en.wikipedia.org/...")
        form_layout.addRow("Wikipedia Link:", self.wikipedia_link_edit)

        layout.addLayout(form_layout)

        # Parent Award (optional)
        parent_layout = QFormLayout()
        self.parent_award_combo = QComboBox()
        self.parent_award_combo.addItem("(None)", None)
        self.load_parent_awards()
        parent_layout.addRow("Parent Award:", self.parent_award_combo)
        layout.addLayout(parent_layout)

        # Button box
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        layout.addWidget(self.button_box)

    def setup_connections(self):
        """Setup signal connections."""
        self.button_box.accepted.connect(self.accept_dialog)
        self.button_box.rejected.connect(self.reject)

        # Enable/disable OK button based on required fields
        self.award_name_edit.textChanged.connect(self.validate_form)

    def load_parent_awards(self):
        """Load existing awards for parent selection."""
        try:
            awards = self.controller.get.get_all_entities("Award")
            for award in awards:
                display_text = f"{award.award_name}"
                if award.award_year:
                    display_text += f" ({award.award_year})"
                if award.award_category:
                    display_text += f" - {award.award_category}"
                self.parent_award_combo.addItem(display_text, award.award_id)
        except Exception as e:
            logger.error(f"Error loading parent awards: {e}")

    def validate_form(self):
        """Validate the form and enable/disable OK button."""
        award_name = self.award_name_edit.text().strip()
        is_valid = bool(award_name)

        ok_button = self.button_box.button(QDialogButtonBox.Ok)
        ok_button.setEnabled(is_valid)

    def accept_dialog(self):
        """Handle dialog acceptance."""
        try:
            # Get form values
            award_name = self.award_name_edit.text().strip()
            award_year = (
                self.award_year_spin.value()
                if self.award_year_spin.value() != 1900
                else None
            )
            award_category = self.award_category_edit.text().strip() or None
            award_description = (
                self.award_description_edit.toPlainText().strip() or None
            )
            wikipedia_link = self.wikipedia_link_edit.text().strip() or None
            parent_id = self.parent_award_combo.currentData()

            # Validate required fields
            if not award_name:
                QMessageBox.warning(self, "Validation Error", "Award name is required.")
                return

            # Create the award
            award = self.controller.add.add_entity(
                "Award",
                award_name=award_name,
                award_year=award_year,
                award_category=award_category,
                award_description=award_description,
                wikipedia_link=wikipedia_link,
                parent_id=parent_id,
            )

            if award and award.award_id:
                # Create the association
                association = self.controller.add.add_entity(
                    "AwardAssociation",
                    award_id=award.award_id,
                    entity_id=self.entity_id,
                    entity_type=self.entity_type,
                    association_type=self.association_type_combo.currentText(),
                )

                if association:
                    logger.info(
                        f"Created award {award.award_name} "
                        f"for {self.entity_type} {self.entity_id}"
                    )
                    self.award_added.emit(award.award_id)
                    self.accept()
                else:
                    QMessageBox.critical(
                        self, "Error", "Failed to create award association."
                    )
            else:
                QMessageBox.critical(self, "Error", "Failed to create award.")

        except Exception as e:
            logger.error(f"Error creating award: {e}")
            QMessageBox.critical(
                self, "Error", f"An error occurred while creating the award:\n{str(e)}"
            )

    def get_award_details(self):
        """Get the created award details (for testing/validation)."""
        return {
            "award_name": self.award_name_edit.text().strip(),
            "award_year": self.award_year_spin.value()
            if self.award_year_spin.value() != 1900
            else None,
            "award_category": self.award_category_edit.text().strip() or None,
            "association_type": self.association_type_combo.currentText(),
        }
