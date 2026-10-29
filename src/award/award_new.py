"""Dialog that creates an award, optionally linked to one entity."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QTextEdit, QVBoxLayout

from src.award.award_common import ASSOCIATION_TYPES, RECIPIENT, YEAR_MAX, YEAR_MIN, award_display_text, parse_award_year
from src.foundation.logger_config import logger


class AddAwardDialog(QDialog):
    """Create a new award and, when an entity is given, link it to that entity."""

    award_added = Signal(int)  # award_id

    def __init__(self, controller, entity_type=None, entity_id=None, parent=None, entity_name=None):
        super().__init__(parent)
        self.controller = controller
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.created_award_id: int | None = None

        self.setWindowTitle(f"Add Award to {entity_type}" if entity_type else "Create New Award")
        self.setMinimumWidth(500)

        self.init_ui()
        self.setup_connections()
        self.validate_form()

    def init_ui(self):
        """Build the award form."""
        layout = QVBoxLayout(self)

        if self.entity_type:
            target = self.entity_name or f"ID {self.entity_id}"
            info_label = QLabel(f"Adding award to {self.entity_type}: {target}")
            info_label.setProperty("textRole", "note")
            layout.addWidget(info_label)

        form_layout = QFormLayout()

        self.award_name_edit = QLineEdit()
        self.award_name_edit.setPlaceholderText("e.g., Grammy Award, Billboard Music Award")
        form_layout.addRow("Award Name*:", self.award_name_edit)

        # Plain text, not a spin box, so "no year" and any historic year are both enterable.
        self.award_year_edit = QLineEdit()
        self.award_year_edit.setPlaceholderText(f"Optional, {YEAR_MIN}-{YEAR_MAX} (e.g. 1959, 2024)")
        form_layout.addRow("Award Year:", self.award_year_edit)

        self.award_category_edit = QLineEdit()
        self.award_category_edit.setPlaceholderText("e.g., Best New Artist, Album of the Year")
        form_layout.addRow("Category:", self.award_category_edit)

        self.association_type_combo = QComboBox()
        self.association_type_combo.addItems(ASSOCIATION_TYPES)
        self.association_type_combo.setCurrentText(RECIPIENT)
        if self.entity_type:
            form_layout.addRow("Association Type:", self.association_type_combo)

        self.award_description_edit = QTextEdit()
        self.award_description_edit.setMaximumHeight(100)
        self.award_description_edit.setPlaceholderText("Optional description of the award...")
        form_layout.addRow("Description:", self.award_description_edit)

        self.wikipedia_link_edit = QLineEdit()
        self.wikipedia_link_edit.setPlaceholderText("https://en.wikipedia.org/...")
        form_layout.addRow("Wikipedia Link:", self.wikipedia_link_edit)

        self.parent_award_combo = QComboBox()
        self.parent_award_combo.addItem("(None - top-level award)", None)
        self.load_parent_awards()
        form_layout.addRow("Parent Award:", self.parent_award_combo)

        layout.addLayout(form_layout)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(self.button_box)

    def setup_connections(self):
        """Connect the buttons and the required-field check."""
        self.button_box.accepted.connect(self.accept_dialog)
        self.button_box.rejected.connect(self.reject)
        self.award_name_edit.textChanged.connect(self.validate_form)

    def load_parent_awards(self):
        """Fill the parent combo with the existing awards, sorted by label."""
        awards = self.controller.get.get_all_entities("Award")
        for award in sorted(awards, key=lambda a: award_display_text(a).casefold()):
            self.parent_award_combo.addItem(award_display_text(award), award.award_id)

    def validate_form(self):
        """Enable OK only when the required award name is present."""
        self.button_box.button(QDialogButtonBox.Ok).setEnabled(bool(self.award_name_edit.text().strip()))

    def accept_dialog(self):
        """Create the award (and the association, if any) and close on success."""
        award_name = self.award_name_edit.text().strip()
        if not award_name:
            QMessageBox.warning(self, "Validation Error", "Award name is required.")
            return
        try:
            award_year = parse_award_year(self.award_year_edit.text())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Year", str(e))
            return

        award = self.controller.add.add_entity(
            "Award",
            award_name=award_name,
            award_year=award_year,
            award_category=self.award_category_edit.text().strip() or None,
            award_description=self.award_description_edit.toPlainText().strip() or None,
            wikipedia_link=self.wikipedia_link_edit.text().strip() or None,
            parent_id=self.parent_award_combo.currentData(),
        )
        if not award:
            QMessageBox.critical(self, "Error", "Failed to create award. See the log for details.")
            return

        if self.entity_type:
            association = self.controller.add.add_entity(
                "AwardAssociation", award_id=award.award_id, entity_id=self.entity_id, entity_type=self.entity_type, association_type=self.association_type_combo.currentText()
            )
            if not association:
                # Remove the new award so a failed link does not leave an orphan.
                self.controller.delete.delete_entity("Award", award.award_id)
                QMessageBox.critical(self, "Error", "Failed to link the award. See the log for details.")
                return

        logger.info(f"Created award {award_name} (target: {self.entity_type} {self.entity_id})")
        self.created_award_id = award.award_id
        self.award_added.emit(award.award_id)
        self.accept()
