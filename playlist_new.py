from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QTextEdit,
)


class PlaylistCreateDialog(QDialog):
    """Dialog for entering new playlist details."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Playlist")
        self.setMinimumWidth(300)

        layout = QFormLayout(self)
        self.name_input = QLineEdit()
        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(100)

        layout.addRow("Playlist Name:", self.name_input)
        layout.addRow("Description:", self.desc_input)

        # OK and Cancel buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def get_data(self):
        return self.name_input.text().strip(), self.desc_input.toPlainText().strip()
