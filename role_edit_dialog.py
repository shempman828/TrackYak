from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from logger_config import logger


class RoleEditDialog(QDialog):
    def __init__(self, controller, role=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.role = role
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
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
        if self.role:
            self.name_input.setText(self.role.role_name)
            self.desc_input.setText(self.role.role_description or "")

    def validate(self):
        name = self.name_input.text().strip()
        description = self.desc_input.text().strip() or None

        if not name:
            QMessageBox.warning(self, "Validation", "Role name is required")
            return

        # Check for duplicate names (across all roles)
        existing_role = self.controller.get.get_entity_object("Role", role_name=name)
        if existing_role and (
            not self.role or existing_role.role_id != self.role.role_id
        ):
            QMessageBox.warning(self, "Validation", "Role name already exists")
            return

        try:
            if self.role:  # Editing
                self.controller.update.update_entity(
                    "Role",
                    self.role.role_id,
                    role_name=name,
                    role_description=description,
                )
            else:  # Creating
                self.controller.add.add_entity(
                    "Role", role_name=name, role_description=description
                )
            self.accept()
        except Exception as e:
            logger.error(f"Error saving role: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save role: {str(e)}")
