from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config_setup import Config


class ConfigDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(600, 500)

        self._setup_ui()
        self._load_current_settings()

    def _show_error(self, message):
        """Show error message"""
        QMessageBox.critical(self, "Error", message)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget for different settings categories
        self.tabs = QTabWidget()

        # Library tab
        self.library_tab = self._create_library_tab()
        self.tabs.addTab(self.library_tab, "Library")

        # Playback tab
        self.playback_tab = self._create_playback_tab()
        self.tabs.addTab(self.playback_tab, "Playback")

        # Appearance tab
        self.appearance_tab = self._create_appearance_tab()
        self.tabs.addTab(self.appearance_tab, "Appearance")

        # Audio tab
        self.audio_tab = self._create_audio_tab()
        self.tabs.addTab(self.audio_tab, "Audio")

        # logging tab
        self.logging_tab = self._create_logging_tab(
            self.tabs.AddTab(self.logging_tab, "Loggin")
        )

        layout.addWidget(self.tabs)

        # Buttons
        button_layout = QHBoxLayout()

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply_settings)

        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self._ok_clicked)
        self.ok_btn.setDefault(True)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.ok_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def _create_library_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Directory selection
        dir_layout = QHBoxLayout()
        self.dir_display = QLabel()
        self.dir_display.setMinimumWidth(300)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_directory)

        dir_layout.addWidget(self.dir_display)
        dir_layout.addWidget(self.browse_btn)
        layout.addRow("Music Library:", dir_layout)

        # Library options
        self.scan_startup_check = QCheckBox("Scan for music on startup")
        layout.addRow("", self.scan_startup_check)

        self.auto_refresh_check = QCheckBox(
            "Auto-refresh library when changes detected"
        )
        layout.addRow("", self.auto_refresh_check)

        return widget

    def _create_playback_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Volume
        volume_layout = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_label = QLabel("75%")
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_label)
        layout.addRow("Volume:", volume_layout)

        # Repeat mode
        self.repeat_combo = QComboBox()
        self.repeat_combo.addItems(["No Repeat", "Repeat One", "Repeat All"])
        layout.addRow("Repeat Mode:", self.repeat_combo)

        # Playback options
        self.shuffle_check = QCheckBox("Shuffle playback")
        layout.addRow("", self.shuffle_check)

        self.crossfade_check = QCheckBox("Crossfade between tracks")
        layout.addRow("", self.crossfade_check)

        # Fade duration
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(0, 10)
        self.fade_spin.setSuffix(" seconds")
        layout.addRow("Fade Duration:", self.fade_spin)

        return widget

    def _create_appearance_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Theme selection
        self.theme_combo = QComboBox()
        available_themes = self.config.get_available_themes()
        self.theme_combo.addItems(
            available_themes if available_themes else ["default.qss"]
        )
        layout.addRow("Theme:", self.theme_combo)

        # Color scheme
        self.color_scheme_combo = QComboBox()
        self.color_scheme_combo.addItems(["Dark", "Light", "System"])
        layout.addRow("Color Scheme:", self.color_scheme_combo)

        return widget

    def _create_audio_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Output device (placeholder - would need platform-specific implementation)
        self.output_combo = QComboBox()
        self.output_combo.addItems(["Default Output"])
        self.output_combo.setEnabled(False)  # Disabled until implemented
        layout.addRow("Output Device:", self.output_combo)

        # Sample rate
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(["44100 Hz", "48000 Hz", "96000 Hz"])
        layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Buffer size
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItems(["256", "512", "1024", "2048"])
        layout.addRow("Buffer Size:", self.buffer_combo)

        return widget

    def _load_current_settings(self):
        """Load current settings into UI"""
        # Library settings
        self.dir_display.setText(str(self.config.get_base_directory()))
        self.scan_startup_check.setChecked(self.config.get_scan_on_startup())
        self.auto_refresh_check.setChecked(self.config.get_auto_refresh())

        # Playback settings
        volume = self.config.get_volume()
        self.volume_slider.setValue(volume)
        self.volume_label.setText(f"{volume}%")

        repeat_mode = self.config.get_repeat_mode()
        repeat_index = {"none": 0, "one": 1, "all": 2}.get(repeat_mode, 0)
        self.repeat_combo.setCurrentIndex(repeat_index)

        self.shuffle_check.setChecked(self.config.get_shuffle())
        self.crossfade_check.setChecked(self.config.get_crossfade())
        self.fade_spin.setValue(self.config.get_fade_duration())

        # Appearance settings
        current_theme = self.config.get_theme_file()
        index = self.theme_combo.findText(current_theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)

        color_scheme = self.config.get_theme()
        scheme_index = {"dark": 0, "light": 1, "system": 2}.get(color_scheme, 0)
        self.color_scheme_combo.setCurrentIndex(scheme_index)

        # Audio settings
        sample_rate = self.config.get_sample_rate()
        rate_text = f"{sample_rate} Hz"
        index = self.sample_rate_combo.findText(rate_text)
        if index >= 0:
            self.sample_rate_combo.setCurrentIndex(index)

        buffer_size = self.config.get_buffer_size()
        index = self.buffer_combo.findText(str(buffer_size))
        if index >= 0:
            self.buffer_combo.setCurrentIndex(index)

        # Connect signals
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_label.setText(f"{v}%")
        )

    def _browse_directory(self):
        """Browse for music directory"""
        current_dir = self.config.get_base_directory()
        directory = QFileDialog.getExistingDirectory(
            self, "Select Music Library Directory", str(current_dir)
        )

        if directory and Path(directory).exists():
            self.dir_display.setText(directory)

    def _apply_settings(self):
        """Apply current settings without closing dialog"""
        try:
            # Library settings
            new_dir = Path(self.dir_display.text())
            if new_dir.exists():
                self.config.set_base_directory(new_dir)
            else:
                QMessageBox.warning(
                    self,
                    "Warning",
                    "Selected directory does not exist. Using current directory.",
                )

            self.config.set_scan_on_startup(self.scan_startup_check.isChecked())
            self.config.set_auto_refresh(self.auto_refresh_check.isChecked())

            # Playback settings
            self.config.set_volume(self.volume_slider.value())

            repeat_modes = {0: "none", 1: "one", 2: "all"}
            self.config.set_repeat_mode(repeat_modes[self.repeat_combo.currentIndex()])

            self.config.set_shuffle(self.shuffle_check.isChecked())
            self.config.set_crossfade(self.crossfade_check.isChecked())
            self.config.set_fade_duration(self.fade_spin.value())

            # Appearance settings
            self.config.set_theme_file(self.theme_combo.currentText())

            color_schemes = {0: "dark", 1: "light", 2: "system"}
            self.config.set_theme(color_schemes[self.color_scheme_combo.currentIndex()])

            # Audio settings
            sample_rate_text = self.sample_rate_combo.currentText()
            sample_rate = int(sample_rate_text.split()[0])
            self.config.set_sample_rate(sample_rate)

            buffer_size = int(self.buffer_combo.currentText())
            self.config.set_buffer_size(buffer_size)

            # Save config
            self.config.save()

            QMessageBox.information(self, "Success", "Settings applied successfully.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply settings: {e}")

    def _ok_clicked(self):
        """Apply settings and close dialog"""
        self._apply_settings()
        self.accept()

    def _create_logging_tab(self):
        """Create logging configuration tab"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Log level
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        layout.addRow("Log Level:", self.log_level_combo)

        # Logging options
        self.console_logging_check = QCheckBox("Enable console logging")
        layout.addRow("", self.console_logging_check)

        self.file_logging_check = QCheckBox("Enable file logging")
        layout.addRow("", self.file_logging_check)

        # File settings
        self.max_file_size_spin = QSpinBox()
        self.max_file_size_spin.setRange(1, 100)
        self.max_file_size_spin.setSuffix(" MB")
        layout.addRow("Max File Size:", self.max_file_size_spin)

        self.backup_count_spin = QSpinBox()
        self.backup_count_spin.setRange(1, 50)
        layout.addRow("Backup Files:", self.backup_count_spin)

        return widget
