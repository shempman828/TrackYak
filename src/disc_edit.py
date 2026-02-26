from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
)


class DiscEditDialog(QDialog):
    """Dialog for adding/editing disc properties"""

    def __init__(self, album, controller, parent=None):
        super().__init__(parent)
        self.album = album
        self.controller = controller
        self.init_ui()

    def init_ui(self):
        """Initialize dialog UI"""
        self.setWindowTitle("Add Disc")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)

        # Form group
        form_group = QGroupBox("Disc Properties")
        form_layout = QFormLayout()

        # Disc title
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Optional disc title")
        form_layout.addRow("Title:", self.title_input)

        # Media type
        self.type_combo = QComboBox()
        self.type_combo.addItems(
            ["CD", "Vinyl", "Cassette", "Digital", "DVD", "Blu-ray", "Other"]
        )
        form_layout.addRow("Media Type:", self.type_combo)

        form_group.setLayout(form_layout)
        layout.addWidget(form_group)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_disc_data(self):
        """Get entered disc data"""
        return {
            "disc_title": self.title_input.text().strip() or None,
            "media_type": self.type_combo.currentText(),
        }
