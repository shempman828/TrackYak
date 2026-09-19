from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


class RoleEditDialog(QDialog):
    """Dialog for creating a new role or editing an existing role's name and description."""

    def __init__(self, controller, role=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.role = role
        # Populated after a successful save so callers (e.g. the "New Parent"/
        # "New Child" context menu actions) can link the resulting role
        # without re-querying the database.
        self.result_role = None
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        """Build the name/description form and OK/Cancel buttons."""
        self.setWindowTitle("Edit Role" if self.role else "New Role")
        self.setMinimumWidth(300)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        layout.addRow("Role Name:", self.name_input)

        # Role description field
        self.desc_input = QLineEdit()
        layout.addRow("Description:", self.desc_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        """Pre-fill the form fields when editing an existing role."""
        if self.role:
            self.name_input.setText(self.role.role_name)
            self.desc_input.setText(self.role.role_description or "")

    def validate(self):
        """Validate the form and save the role, or show an error and leave the dialog open."""
        name = self.name_input.text().strip()
        description = self.desc_input.text().strip() or None

        if not name:
            QMessageBox.warning(self, "Validation", "Role name is required")
            return

        # Check for duplicate names (across all roles)
        existing_role = self.controller.get.get_entity_object("Role", role_name=name)
        if existing_role and (not self.role or existing_role.role_id != self.role.role_id):
            QMessageBox.warning(self, "Validation", "Role name already exists")
            return

        try:
            if self.role:  # Editing
                self.controller.update.update_entity(
                    "Role", self.role.role_id, role_name=name, role_description=description
                )
                self.result_role = self.role
            else:  # Creating
                self.result_role = self.controller.add.add_entity(
                    "Role", role_name=name, role_description=description
                )
            self.accept()
        except SQLAlchemyError as e:
            logger.error(f"Error saving role: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to save role: {e!s}")
