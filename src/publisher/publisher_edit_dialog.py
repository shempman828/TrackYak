from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from src.core.logger_config import logger


class PublisherEditDialog(QDialog):
    """Simple name/description dialog for creating a new publisher."""

    def __init__(self, controller, publisher=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.publisher = publisher
        # Populated after a successful save so callers (e.g. the "New Parent"/
        # "New Child" context menu actions) can link the resulting publisher
        # without re-querying the database.
        self.result_publisher = None
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        self.setWindowTitle("Edit Publisher" if self.publisher else "New Publisher")
        self.setMinimumWidth(300)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        layout.addRow("Publisher Name:", self.name_input)

        self.desc_input = QLineEdit()
        layout.addRow("Description:", self.desc_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        if self.publisher:
            self.name_input.setText(self.publisher.publisher_name or "")
            self.desc_input.setText(self.publisher.description or "")

    def validate(self):
        name = self.name_input.text().strip()
        description = self.desc_input.text().strip() or None

        if not name:
            QMessageBox.warning(self, "Validation", "Publisher name is required")
            return

        existing = self.controller.get.get_entity_object(
            "Publisher", publisher_name=name
        )
        if existing and (
            not self.publisher or existing.publisher_id != self.publisher.publisher_id
        ):
            QMessageBox.warning(self, "Validation", "Publisher name already exists")
            return

        try:
            if self.publisher:  # Editing
                self.controller.update.update_entity(
                    "Publisher",
                    self.publisher.publisher_id,
                    publisher_name=name,
                    description=description,
                )
                self.result_publisher = self.publisher
            else:  # Creating
                self.result_publisher = self.controller.add.add_entity(
                    "Publisher", publisher_name=name, description=description
                )
            self.accept()
        except Exception as e:
            logger.error(f"Error saving publisher: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save publisher: {str(e)}")
