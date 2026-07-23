import os

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.common.entity_alias_tab import EntityAliasesTab
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.core.asset_paths import icon
from src.core.logger_config import logger
from src.publisher.publisher_hierarchy import get_descendant_publisher_ids
from src.publisher.publisher_image_manager import move_to_publisher_logos_dir

_SETTINGS_LAST_LOGO_DIR = "publisher_editor/last_logo_dir"
LOGO_MAX_SIZE = QSize(100, 100)


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
        self._logo_path = None
        self._settings = QSettings()
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

        # Parent publisher picker. Excludes this publisher and its own
        # descendants from the index so a completion can never introduce a
        # cycle in the hierarchy.
        self.parent_edit = EntityCompleterEdit("Search parent publisher…")
        excluded_ids = set()
        if self.publisher:
            excluded_ids = set(
                get_descendant_publisher_ids(
                    self.controller, self.publisher.publisher_id
                )
            )
        publishers = self.controller.get.get_all_entities("Publisher")
        # Kept unfiltered (unlike parent_index below) so find-or-create in
        # validate() can still recognize an excluded descendant by name --
        # the cycle check there is what rejects it, not a missing lookup.
        self._known_publishers = publishers
        parent_index = {
            p.publisher_name: p.publisher_id
            for p in publishers
            if p.publisher_name and p.publisher_id not in excluded_ids
        }
        self.parent_edit.set_index(parent_index)
        self.parent_edit.returnPressed.connect(self.validate)
        layout.addRow("Parent Publisher:", self.parent_edit)

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

        # Logo picker only makes sense once the publisher exists -- like
        # the artist profile picture, the picked file is moved into the
        # managed images dir immediately using publisher_id in its
        # filename, so this section is edit-only.
        if self.publisher:
            self.logo_label = QLabel()
            self.logo_label.setFixedSize(LOGO_MAX_SIZE)
            self.logo_label.setAlignment(Qt.AlignCenter)
            self.logo_label.setStyleSheet("border: 1px solid palette(mid);")

            logo_buttons = QVBoxLayout()
            self.logo_browse_button = QPushButton("Browse...")
            self.logo_browse_button.clicked.connect(self._browse_logo)
            self.logo_clear_button = QPushButton("Clear")
            self.logo_clear_button.clicked.connect(self._clear_logo)
            logo_buttons.addWidget(self.logo_browse_button)
            logo_buttons.addWidget(self.logo_clear_button)
            logo_buttons.addStretch()

            logo_row = QHBoxLayout()
            logo_row.addWidget(self.logo_label)
            logo_row.addLayout(logo_buttons)
            logo_row.addStretch()
            layout.addRow("Logo:", logo_row)

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
            if self.publisher.parent_id:
                parent = self.controller.get.get_entity_object(
                    "Publisher", publisher_id=self.publisher.parent_id
                )
                self.parent_edit.setText(parent.publisher_name if parent else "")
            self.begin_year_edit.set_from_db(self.publisher.begin_year)
            self.end_year_edit.set_from_db(self.publisher.end_year)
            self.is_active_check.setChecked(bool(self.publisher.is_active))
            self.wiki_input.setText(self.publisher.wikipedia_link or "")
            self.is_fixed_check.setChecked(bool(self.publisher.is_fixed))
            self._set_logo_path(self.publisher.logo_path)
            if self.tab_aliases:
                self.tab_aliases.load(self.publisher)
        else:
            self.is_active_check.setChecked(True)

    def _refresh_logo_preview(self, path):
        if path and os.path.exists(path):
            pixmap = QPixmap(path)
        else:
            pixmap = icon("default_logo.svg").pixmap(LOGO_MAX_SIZE)
        scaled = pixmap.scaled(LOGO_MAX_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.logo_label.setPixmap(scaled)

    def _set_logo_path(self, path):
        self._logo_path = path or None
        self._refresh_logo_preview(self._logo_path)

    def _browse_logo(self):
        start_dir = self._settings.value(_SETTINGS_LAST_LOGO_DIR, "", type=str)
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Publisher Logo",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg)",
        )
        if path:
            self._settings.setValue(_SETTINGS_LAST_LOGO_DIR, os.path.dirname(path))
            managed_path = move_to_publisher_logos_dir(
                self.publisher.publisher_id, self.publisher.publisher_name, path
            )
            self._set_logo_path(managed_path)

    def _clear_logo(self):
        self._set_logo_path(None)

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

        parent_name = self.parent_edit.text().strip()
        parent_id = None
        if parent_name:
            # Prefer the id locked in when the user picked a completion --
            # falls back to a name lookup for the edit-mode prefill, which
            # sets the text directly and never locks an id.
            parent_id = self.parent_edit.matched_id()
            if parent_id is None:
                parent_obj = self.controller.get.get_entity_object(
                    "Publisher", publisher_name=parent_name
                )
                parent_id = parent_obj.publisher_id if parent_obj else None
            if parent_id is None:
                # No existing publisher matches what was typed -- create one
                # on the fly rather than blocking the save, mirroring the
                # find-or-create pattern used by the other completer fields.
                parent_obj = find_or_create_by_name(
                    self.controller,
                    "Publisher",
                    "publisher_name",
                    parent_name,
                    self._known_publishers,
                )
                parent_id = parent_obj.publisher_id if parent_obj else None
            if self.publisher and parent_id in get_descendant_publisher_ids(
                self.controller, self.publisher.publisher_id
            ):
                QMessageBox.warning(
                    self,
                    "Invalid Parent",
                    "Cannot set parent to this publisher itself or one of its own descendants.",
                )
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
                    parent_id=parent_id,
                    begin_year=begin_year,
                    end_year=end_year,
                    is_active=is_active,
                    wikipedia_link=wikipedia_link,
                    is_fixed=is_fixed,
                    logo_path=self._logo_path,
                )
                self.result_publisher = self.publisher
            else:  # Creating
                self.result_publisher = self.controller.add.add_entity(
                    "Publisher",
                    publisher_name=name,
                    description=description,
                    parent_id=parent_id,
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
