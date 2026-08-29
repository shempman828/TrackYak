from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
)

from src.core.logger_config import logger


class DiscEditDialog(QDialog):
    """Dialog for adding/editing disc properties"""

    def __init__(self, album, controller, parent=None, disc=None):
        super().__init__(parent)
        self.album = album
        self.controller = controller
        self.disc = disc
        logger.debug(
            f"Opening disc {'edit' if disc else 'add'} dialog for album "
            f"{getattr(album, 'album_name', album)}"
        )
        self.init_ui()

    def init_ui(self):
        """Initialize dialog UI"""
        self.setWindowTitle("Edit Disc" if self.disc is not None else "Add Disc")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)

        # Form group
        form_group = QGroupBox("Disc Properties")
        form_layout = QFormLayout()

        # Disc title
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Optional disc title")
        if self.disc is not None and self.disc.disc_title:
            self.title_input.setText(self.disc.disc_title)
        form_layout.addRow("Title:", self.title_input)

        form_group.setLayout(form_layout)
        layout.addWidget(form_group)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_disc_data(self):
        """Get entered disc data"""
        return {"disc_title": self.title_input.text().strip() or None}
