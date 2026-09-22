from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout

from src.foundation.logger_config import logger

# ---------------------------------------------------------------------------
# Helper dialog: New Album
# ---------------------------------------------------------------------------


class NewAlbumDialog(QDialog):
    """Simple dialog to create a new blank album."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("New Album")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        heading = QLabel("New Album")
        heading.setProperty("title", True)
        layout.addWidget(heading)

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("NewAlbumNameField")
        self.name_edit.setPlaceholderText("Album name (required)")
        layout.addWidget(self.name_edit)

        form = QFormLayout()
        form.setSpacing(10)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 9999)
        self.year_spin.setValue(0)
        self.year_spin.setSpecialValueText("Unknown")
        form.addRow("Release Year:", self.year_spin)

        self.artist_edit = QLineEdit()
        self.artist_edit.setPlaceholderText("Optional — leave blank to add later")
        form.addRow("Artist:", self.artist_edit)

        self.compilation_check = QCheckBox("This is a compilation")
        form.addRow("", self.compilation_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        ok_button.setText("Create")
        ok_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
            logger.debug("New album dialog rejected: album name is empty")
            QMessageBox.warning(self, "Required", "Album name cannot be empty.")
            return
        self.accept()

    # ── Public accessors ──────────────────────────────────────────────────
    @property
    def album_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def release_year(self):
        v = self.year_spin.value()
        return v if v > 0 else None

    @property
    def artist_name(self) -> str:
        return self.artist_edit.text().strip()

    @property
    def is_compilation(self) -> int:
        return 1 if self.compilation_check.isChecked() else 0
