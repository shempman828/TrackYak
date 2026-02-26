from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,  # Add this
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,  # Add this for license display
    QVBoxLayout,
)

from asset_paths import icon
from config_setup import Config


class StartupDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("First Run Setup - TrackYak")
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Logo
        logo_label = QLabel()
        logo_pix = icon("splash.png").pixmap(200, 200)
        logo_label.setPixmap(logo_pix)
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        # Welcome message
        welcome_label = QLabel(
            "<h1>Welcome to TrackYak</h1>"
            "<p>Let's adjust a few settings before we start.</p>"
        )
        welcome_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(welcome_label)

        # Directory selection frame
        dir_frame = QFrame()
        dir_frame.setFrameStyle(QFrame.StyledPanel)
        dir_layout = QFormLayout(dir_frame)

        self.dir_display = QLabel()
        base = self.config.get_base_directory()
        self.dir_display.setText(str(base) if base else "No directory selected")
        self.dir_display.setTextInteractionFlags(Qt.TextSelectableByMouse)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_directory)

        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_display)
        dir_row.addWidget(browse_btn)

        dir_layout.addRow("Select main music directory:", dir_row)
        layout.addWidget(dir_frame)

        # Theme selection
        theme_frame = QFrame()
        theme_frame.setFrameStyle(QFrame.StyledPanel)
        theme_layout = QFormLayout(theme_frame)

        self.theme_combo = QComboBox()
        themes = self.config.get_available_themes()
        if themes:
            self.theme_combo.addItems(themes)
            default_theme = self.config.get_theme_file()
            idx = self.theme_combo.findText(default_theme)
            if idx >= 0:
                self.theme_combo.setCurrentIndex(idx)
        else:
            self.theme_combo.addItem("default.qss")
            self.theme_combo.setEnabled(False)

        theme_layout.addRow("Preferred theme:", self.theme_combo)
        layout.addWidget(theme_frame)

        # ===== LICENSE AGREEMENT SECTION =====
        license_frame = QFrame()
        license_frame.setFrameStyle(QFrame.StyledPanel)
        license_layout = QVBoxLayout(license_frame)

        # Link to view license
        license_link = QLabel('<a href="#view_license">View License Agreement</a>')
        license_link.setTextFormat(Qt.RichText)
        license_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        license_link.setOpenExternalLinks(False)
        license_link.linkActivated.connect(self._show_license)
        license_layout.addWidget(license_link)

        # License checkbox
        self.license_checkbox = QCheckBox(
            "I have read and agree to the terms of the License Agreement"
        )
        self.license_checkbox.stateChanged.connect(self._update_finish_button)
        license_layout.addWidget(self.license_checkbox)

        layout.addWidget(license_frame)
        # ====================================

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.finish_btn = QPushButton("Finish Setup")
        self.finish_btn.clicked.connect(self._finish_setup)
        self.finish_btn.setDefault(True)
        self.finish_btn.setEnabled(False)  # Disabled until checkbox is checked
        button_row.addWidget(self.finish_btn)

        layout.addLayout(button_row)

    def _update_finish_button(self):
        """Enable finish button only when license is agreed to"""
        self.finish_btn.setEnabled(self.license_checkbox.isChecked())

    def _show_license(self):
        """Show license in a dialog"""
        license_dialog = QDialog(self)
        license_dialog.setWindowTitle("License Agreement")
        license_dialog.setMinimumSize(600, 400)

        layout = QVBoxLayout(license_dialog)

        # Try to load license.md
        license_text = QTextBrowser()
        license_text.setReadOnly(True)

        license_path = Path(__file__).parent.parent / "license.md"
        if license_path.exists():
            try:
                license_content = license_path.read_text(encoding="utf-8")
                license_text.setMarkdown(license_content)
            except Exception as e:
                license_text.setText(f"Could not load license file: {e}")
        else:
            license_text.setText("License file (license.md) not found.")

        layout.addWidget(license_text)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(license_dialog.accept)
        close_btn.setDefault(True)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        license_dialog.exec()

    def _browse_directory(self):
        """Browse for music directory"""
        current_dir = self.config.get_base_directory()
        directory = QFileDialog.getExistingDirectory(
            self, "Select Music Library Directory", str(current_dir)
        )

        if directory and Path(directory).exists():
            self.dir_display.setText(directory)

    def _finish_setup(self):
        """Save settings and close dialog"""
        selected_dir = Path(self.dir_display.text())

        if not selected_dir.exists():
            # Create directory if it doesn't exist
            try:
                selected_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self._show_error(f"Could not create directory: {e}")
                return

        # Save settings
        self.config.set_base_directory(selected_dir)
        self.config.set_theme_file(self.theme_combo.currentText())
        self.config.set_first_run(False)
        self.config.save()

        self.accept()
