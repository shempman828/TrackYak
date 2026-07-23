from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from src.common.entity_alias_tab import EntityAliasesTab
from src.core.logger_config import logger


class OptionalIntEdit(QLineEdit):
    """A QLineEdit that only accepts integers and returns None when empty."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(60)
        self.setAlignment(Qt.AlignCenter)
        self.setValidator(QIntValidator(0, 9999, self))

    def get_value_or_none(self):
        text = self.text().strip()
        return int(text) if text else None

    def set_from_db(self, val):
        self.setText(str(int(val)) if val is not None else "")


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
        self.tab_aliases = None
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

        self.begin_year_edit = OptionalIntEdit("YYYY")
        layout.addRow("Begin Year:", self.begin_year_edit)

        self.end_year_edit = OptionalIntEdit("YYYY")
        layout.addRow("End Year:", self.end_year_edit)

        self.is_active_check = QCheckBox("Active")
        layout.addRow("Status:", self.is_active_check)

        self.wiki_input = QLineEdit()
        self.wiki_input.setPlaceholderText("https://en.wikipedia.org/...")
        layout.addRow("Wikipedia:", self.wiki_input)

        self.is_fixed_check = QCheckBox("Mark metadata as complete")
        layout.addRow("Metadata Complete:", self.is_fixed_check)

        # Aliases only make sense once the publisher exists (they need a
        # publisher_id to point at), so this section is edit-only.
        if self.publisher:
            self.setMinimumSize(420, 420)
            layout.addRow(QLabel("Aliases:"))
            self.tab_aliases = EntityAliasesTab(
                self.controller,
                self.publisher,
                "Publisher",
                "publisher_id",
                placeholder="e.g. EMI Records",
            )
            layout.addRow(self.tab_aliases)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def load_data(self):
        if self.publisher:
            self.name_input.setText(self.publisher.publisher_name or "")
            self.desc_input.setText(self.publisher.description or "")
            self.begin_year_edit.set_from_db(self.publisher.begin_year)
            self.end_year_edit.set_from_db(self.publisher.end_year)
            self.is_active_check.setChecked(bool(self.publisher.is_active))
            self.wiki_input.setText(self.publisher.wikipedia_link or "")
            self.is_fixed_check.setChecked(bool(self.publisher.is_fixed))
            if self.tab_aliases:
                self.tab_aliases.load(self.publisher)
        else:
            self.is_active_check.setChecked(True)

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

        begin_year = self.begin_year_edit.get_value_or_none()
        end_year = self.end_year_edit.get_value_or_none()
        is_active = 1 if self.is_active_check.isChecked() else 0
        wikipedia_link = self.wiki_input.text().strip() or None
        is_fixed = 1 if self.is_fixed_check.isChecked() else 0

        try:
            if self.publisher:  # Editing
                self.controller.update.update_entity(
                    "Publisher",
                    self.publisher.publisher_id,
                    publisher_name=name,
                    description=description,
                    begin_year=begin_year,
                    end_year=end_year,
                    is_active=is_active,
                    wikipedia_link=wikipedia_link,
                    is_fixed=is_fixed,
                )
                self.result_publisher = self.publisher
            else:  # Creating
                self.result_publisher = self.controller.add.add_entity(
                    "Publisher",
                    publisher_name=name,
                    description=description,
                    begin_year=begin_year,
                    end_year=end_year,
                    is_active=is_active,
                    wikipedia_link=wikipedia_link,
                    is_fixed=is_fixed,
                )
            self.accept()
        except Exception as e:
            logger.error(f"Error saving publisher: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save publisher: {str(e)}")
