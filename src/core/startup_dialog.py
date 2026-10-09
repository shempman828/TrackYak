"""First-run setup dialog: library directory, theme and license agreement."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

from src.foundation.asset_paths import BASE_DIR, icon
from src.foundation.config_setup import Config
from src.foundation.logger_config import logger

LICENSE_FILE = BASE_DIR / "license.md"


class StartupDialog(QDialog):
    """Modal first-run dialog that saves the library directory and theme, and records license consent."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        base = self.config.get_base_directory()
        self._selected_dir: Path | None = Path(base) if base else None
        self.setWindowTitle("First Run Setup - TrackYak")
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self):
        """Build the logo, directory, theme, license and button rows."""
        layout = QVBoxLayout(self)

        logo_label = QLabel()
        logo_label.setPixmap(icon("splash.png").pixmap(200, 200))
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        welcome_label = QLabel("<h1>Welcome to TrackYak</h1><p>Let's adjust a few settings before we start.</p>")
        welcome_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(welcome_label)

        # Directory selection
        dir_frame = QFrame()
        dir_frame.setFrameStyle(QFrame.StyledPanel)
        dir_layout = QFormLayout(dir_frame)

        self.dir_display = QLabel()
        self.dir_display.setText(str(self._selected_dir) if self._selected_dir else "No directory selected")
        self.dir_display.setTextInteractionFlags(Qt.TextSelectableByMouse)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_directory)

        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_display)
        dir_row.addWidget(browse_btn)

        dir_layout.addRow("Select main music directory:", dir_row)
        layout.addWidget(dir_frame)

        # Theme selection -- shows stems, same as General Settings
        theme_frame = QFrame()
        theme_frame.setFrameStyle(QFrame.StyledPanel)
        theme_layout = QFormLayout(theme_frame)

        self.theme_combo = QComboBox()
        themes = sorted(Path(name).stem for name in self.config.get_available_themes())
        if themes:
            self.theme_combo.addItems(themes)
            idx = self.theme_combo.findText(Path(self.config.get_theme_file()).stem)
            if idx >= 0:
                self.theme_combo.setCurrentIndex(idx)
        else:
            self.theme_combo.addItem("default")
            self.theme_combo.setEnabled(False)

        theme_layout.addRow("Preferred theme:", self.theme_combo)
        layout.addWidget(theme_frame)

        # License agreement
        license_frame = QFrame()
        license_frame.setFrameStyle(QFrame.StyledPanel)
        license_layout = QVBoxLayout(license_frame)

        license_link = QLabel('<a href="#view_license">View License Agreement</a>')
        license_link.setTextFormat(Qt.RichText)
        license_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        license_link.setOpenExternalLinks(False)
        license_link.linkActivated.connect(self._show_license)
        license_layout.addWidget(license_link)

        self.license_checkbox = QCheckBox("I have read and agree to the terms of the License Agreement")
        self.license_checkbox.stateChanged.connect(self._update_finish_button)
        license_layout.addWidget(self.license_checkbox)

        layout.addWidget(license_frame)

        button_row = QHBoxLayout()
        button_row.addStretch()

        self.finish_btn = QPushButton("Finish Setup")
        self.finish_btn.clicked.connect(self._finish_setup)
        self.finish_btn.setDefault(True)
        self.finish_btn.setEnabled(False)  # enabled once the license is agreed to
        button_row.addWidget(self.finish_btn)

        layout.addLayout(button_row)

    def _show_error(self, message: str):
        """Show a modal error message."""
        QMessageBox.critical(self, "Error", message)

    def _update_finish_button(self):
        """Enable the finish button only when the license is agreed to."""
        self.finish_btn.setEnabled(self.license_checkbox.isChecked())

    def _show_license(self):
        """Show the license text in a modal dialog."""
        license_dialog = QDialog(self)
        license_dialog.setWindowTitle("License Agreement")
        license_dialog.setMinimumSize(600, 400)

        layout = QVBoxLayout(license_dialog)

        license_text = QTextBrowser()
        license_text.setReadOnly(True)

        if LICENSE_FILE.exists():
            try:
                license_text.setMarkdown(LICENSE_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as e:
                logger.error(f"Could not load license file: {e}")
                license_text.setText(f"Could not load license file: {e}")
        else:
            license_text.setText("License file (license.md) not found.")

        layout.addWidget(license_text)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(license_dialog.accept)
        close_btn.setDefault(True)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        license_dialog.exec()

    def _browse_directory(self):
        """Pick the music library directory."""
        start = str(self._selected_dir) if self._selected_dir else str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Select Music Library Directory", start)

        if directory and Path(directory).exists():
            self._selected_dir = Path(directory)
            self.dir_display.setText(directory)

    def _finish_setup(self):
        """Save the settings and close the dialog."""
        selected_dir = self._selected_dir
        if selected_dir is None:
            self._show_error("Select a music library directory first.")
            return

        if not selected_dir.exists():
            try:
                selected_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.error(f"Could not create music directory {selected_dir}: {e}")
                self._show_error(f"Could not create directory: {e}")
                return

        theme_name = self.theme_combo.currentText()
        self.config.set_base_directory(selected_dir)
        # Both keys name the active theme until bugs.md #596 merges them.
        self.config.set_theme_file(f"{theme_name}.qss")
        self.config.set_display_theme(theme_name)
        self.config.set_first_run(False)
        self.config.save()

        logger.info(f"First-run setup completed: library directory set to {selected_dir}")
        self.accept()
