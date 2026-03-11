from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)


# ---------------------------------------------------------------------------
# Helper dialog: New Album
# ---------------------------------------------------------------------------


class NewAlbumDialog(QDialog):
    """Simple dialog to create a new blank album."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("New Album")
        self.setMinimumWidth(400)
        self._build_ui()
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._do_resize_art)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Required")
        form.addRow("Album Name:", self.name_edit)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 9999)
        self.year_spin.setValue(0)
        self.year_spin.setSpecialValueText("Unknown")
        form.addRow("Release Year:", self.year_spin)

        self.artist_edit = QLineEdit()
        self.artist_edit.setPlaceholderText("Optional – leave blank to add later")
        form.addRow("Artist:", self.artist_edit)

        self.compilation_check = QCheckBox()
        form.addRow("Compilation:", self.compilation_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.name_edit.text().strip():
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
