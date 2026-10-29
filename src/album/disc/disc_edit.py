"""Dialog for adding or editing one disc of an album."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QVBoxLayout

from src.foundation.logger_config import logger

MAX_DISC_NUMBER = 999


class DiscEditDialog(QDialog):
    """Dialog for adding/editing disc properties."""

    def __init__(self, album, parent=None, disc=None, next_number=1, taken_numbers=()):
        super().__init__(parent)
        self.album = album
        self.disc = disc
        self.next_number = next_number
        # Numbers used by the album's other discs; the edited disc's own number stays free.
        self.taken_numbers = set(taken_numbers) - ({disc.disc_number} if disc is not None else set())
        logger.debug(f"Opening disc {'edit' if disc else 'add'} dialog for album {getattr(album, 'album_name', album)}")
        self.init_ui()

    def init_ui(self):
        """Build the number/title form, the error label, and the OK/Cancel buttons."""
        self.setWindowTitle("Edit Disc" if self.disc is not None else "Add Disc")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)

        form_group = QGroupBox("Disc Properties")
        form_layout = QFormLayout()

        self.number_input = QSpinBox()
        self.number_input.setRange(1, MAX_DISC_NUMBER)
        self.number_input.setValue(self.disc.disc_number if self.disc is not None and self.disc.disc_number else self.next_number)
        self.number_input.setAccessibleName("Disc number")
        form_layout.addRow("Number:", self.number_input)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Optional disc title")
        self.title_input.setAccessibleName("Disc title")
        if self.disc is not None and self.disc.disc_title:
            self.title_input.setText(self.disc.disc_title)
        form_layout.addRow("Title:", self.title_input)

        form_group.setLayout(form_layout)
        layout.addWidget(form_group)

        self.error_label = QLabel()
        self.error_label.setProperty("textRole", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        """Close only when the disc number is free on this album."""
        number = self.number_input.value()
        if number in self.taken_numbers:
            self.error_label.setText(f"Disc {number} already exists on this album. Choose another number.")
            self.error_label.show()
            self.number_input.setFocus()
            return
        super().accept()

    def get_disc_data(self):
        """Return the entered disc number and title (empty title becomes None)."""
        return {"disc_number": self.number_input.value(), "disc_title": self.title_input.text().strip() or None}
