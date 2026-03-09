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

from src.config_setup import Config


class ConfigDialog(QDialog):
    """General Settings dialog — covers Library, Playback, Appearance, Audio, and Logging."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("General Settings")
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

        # Logging tab — fixed: was calling self.tabs.AddTab inside _create_logging_tab
        self.logging_tab = self._create_logging_tab()
        self.tabs.addTab(self.logging_tab, "Logging")

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

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

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
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_label.setText(f"{v}%")
        )
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_label)
        layout.addRow("Default Volume:", volume_layout)

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

        # Queue persistence
        self.queue_persist_check = QCheckBox("Remember queue between sessions")
        layout.addRow("Queue:", self.queue_persist_check)

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

        # Note to user
        note = QLabel(
            "Detailed audio device settings (device selection, crossfade, normalization) "
            "are available in the Audio menu → Manage Audio Settings."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addRow(note)

        # Sample rate
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(["44100 Hz", "48000 Hz", "96000 Hz"])
        layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Buffer size
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItems(["256", "512", "1024", "2048"])
        layout.addRow("Buffer Size:", self.buffer_combo)

        return widget

    def _create_logging_tab(self):
        """Create logging / debugging configuration tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Log level — this is the "debugging level" setting
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_combo.setToolTip(
            "DEBUG shows the most detail. INFO is recommended for normal use. "
            "WARNING and above only show problems."
        )
        layout.addRow("Log Level:", self.log_level_combo)

        # Logging destinations
        self.console_logging_check = QCheckBox("Enable console logging")
        layout.addRow("", self.console_logging_check)

        self.file_logging_check = QCheckBox("Enable file logging")
        layout.addRow("", self.file_logging_check)

        # File settings
        self.max_file_size_spin = QSpinBox()
        self.max_file_size_spin.setRange(1, 100)
        self.max_file_size_spin.setSuffix(" MB")
        layout.addRow("Max Log File Size:", self.max_file_size_spin)

        self.backup_count_spin = QSpinBox()
        self.backup_count_spin.setRange(1, 50)
        self.backup_count_spin.setToolTip(
            "How many old log files to keep before deleting the oldest."
        )
        layout.addRow("Log Backup Files:", self.backup_count_spin)

        return widget

    # ------------------------------------------------------------------
    # Load / Apply
    # ------------------------------------------------------------------

    def _load_current_settings(self):
        """Load current settings from config into UI controls."""
        try:
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

            # Queue persistence
            self.queue_persist_check.setChecked(
                self.config.config.getboolean("queue", "persist_queue", fallback=True)
            )

            # Appearance settings
            theme_file = self.config.get_theme_file()
            index = self.theme_combo.findText(theme_file)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)

            color_scheme = self.config.get_theme()
            color_index = {"dark": 0, "light": 1, "system": 2}.get(color_scheme, 0)
            self.color_scheme_combo.setCurrentIndex(color_index)

            # Audio settings
            sample_rate = self.config.get_sample_rate()
            rate_map = {44100: 0, 48000: 1, 96000: 2}
            self.sample_rate_combo.setCurrentIndex(rate_map.get(sample_rate, 0))

            buffer_size = self.config.get_buffer_size()
            buffer_map = {256: 0, 512: 1, 1024: 2, 2048: 3}
            self.buffer_combo.setCurrentIndex(buffer_map.get(buffer_size, 2))

            # Logging settings
            import logging as _logging

            level_int = self.config.get_logging_level()
            level_name = _logging.getLevelName(level_int)
            log_index = self.log_level_combo.findText(level_name)
            if log_index >= 0:
                self.log_level_combo.setCurrentIndex(log_index)

            self.console_logging_check.setChecked(
                self.config.is_console_logging_enabled()
            )
            self.file_logging_check.setChecked(self.config.is_file_logging_enabled())
            self.max_file_size_spin.setValue(self.config.get_max_file_size_mb())
            self.backup_count_spin.setValue(self.config.get_backup_count())

        except Exception as e:
            self._show_error(f"Failed to load settings: {e}")

    def _apply_settings(self):
        """Save all settings from the UI back to config."""
        try:
            # Library settings
            dir_text = self.dir_display.text()
            if dir_text and Path(dir_text).exists():
                self.config.set_base_directory(dir_text)
            else:
                QMessageBox.warning(
                    self,
                    "Invalid Directory",
                    "The selected music directory does not exist. Using current directory.",
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

            # Queue persistence
            self.config.config.set(
                "queue",
                "persist_queue",
                str(self.queue_persist_check.isChecked()).lower(),
            )

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

            # Logging settings
            self.config.set_logging_level(self.log_level_combo.currentText())
            self.config.set_console_logging_enabled(
                self.console_logging_check.isChecked()
            )
            self.config.set_file_logging_enabled(self.file_logging_check.isChecked())
            self.config.set_max_file_size_mb(self.max_file_size_spin.value())
            self.config.set_backup_count(self.backup_count_spin.value())

            # Save everything to disk
            self.config.save()

            # Reconfigure logging immediately so the new level takes effect right away
            from src.logger_config import reconfigure_logging

            reconfigure_logging(self.config)

            QMessageBox.information(self, "Success", "Settings saved successfully.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply settings: {e}")

    def _ok_clicked(self):
        """Apply settings and close dialog."""
        self._apply_settings()
        self.accept()

    def _browse_directory(self):
        """Open a folder picker and update the directory display."""
        current = self.dir_display.text()
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Music Library Directory",
            current if current else str(Path.home()),
        )
        if directory:
            self.dir_display.setText(directory)
