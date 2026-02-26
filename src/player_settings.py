"""Audio Settings Dialog"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from logger_config import logger


class AudioSettingsDialog(QDialog):
    """Dialog for configuring audio settings including device, crossfade, normalization, and exclusive mode."""

    # Signals for when settings change
    audio_device_changed = Signal(str)
    crossfade_changed = Signal(bool)
    crossfade_duration_changed = Signal(int)
    normalization_changed = Signal(bool)
    normalization_target_changed = Signal(float)
    exclusive_mode_changed = Signal(bool)

    def __init__(self, music_player, parent=None):
        super().__init__(parent)
        self.music_player = music_player

        self.setWindowTitle("Audio Settings")
        self.setMinimumWidth(500)
        self.setModal(True)

        self._setup_ui()
        self._load_current_settings()

    def _setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)

        # Audio Device Group
        device_group = QGroupBox("Audio Output Device")
        device_layout = QFormLayout(device_group)

        self.device_combo = QComboBox()
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        device_layout.addRow("Output Device:", self.device_combo)

        self.exclusive_mode_check = QCheckBox("Exclusive Mode")
        self.exclusive_mode_check.setToolTip(
            "Enable exclusive mode to give this application full control over your audio device. Other programs will be unable to play sound while this is active, which can reduce latency and prevent audio glitches."
        )
        self.exclusive_mode_check.toggled.connect(self.exclusive_mode_changed.emit)
        device_layout.addRow(self.exclusive_mode_check)

        layout.addWidget(device_group)

        # Crossfade Group
        crossfade_group = QGroupBox("Crossfade Settings")
        crossfade_layout = QVBoxLayout(crossfade_group)

        self.crossfade_check = QCheckBox("Enable Crossfade Between Tracks")
        self.crossfade_check.toggled.connect(self._on_crossfade_toggled)
        crossfade_layout.addWidget(self.crossfade_check)

        # Crossfade duration controls
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Crossfade Duration:"))

        self.crossfade_spinbox = QSpinBox()
        self.crossfade_spinbox.setRange(0, 10000)  # 0-10 seconds
        self.crossfade_spinbox.setSingleStep(100)  # 100ms steps
        self.crossfade_spinbox.setSuffix(" ms")
        self.crossfade_spinbox.valueChanged.connect(
            self.crossfade_duration_changed.emit
        )
        duration_layout.addWidget(self.crossfade_spinbox)

        duration_layout.addStretch()
        crossfade_layout.addLayout(duration_layout)

        layout.addWidget(crossfade_group)

        # Normalization Group
        normalization_group = QGroupBox("Loudness Normalization")
        normalization_layout = QVBoxLayout(normalization_group)

        self.normalization_check = QCheckBox("Enable Loudness Normalization")
        self.normalization_check.toggled.connect(self._on_normalization_toggled)
        normalization_layout.addWidget(self.normalization_check)

        # Normalization target controls
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("Target Loudness:"))

        self.normalization_spinbox = QDoubleSpinBox()
        self.normalization_spinbox.setRange(-50.0, -5.0)  # Reasonable LUFS range
        self.normalization_spinbox.setSingleStep(0.5)
        self.normalization_spinbox.setSuffix(" LUFS")
        self.normalization_spinbox.valueChanged.connect(
            self.normalization_target_changed.emit
        )
        target_layout.addWidget(self.normalization_spinbox)

        target_layout.addStretch()
        normalization_layout.addLayout(target_layout)

        # Add info label about normalization
        info_label = QLabel(
            "Normalization adjusts track volumes to a consistent loudness level. "
            "Uses ReplayGain metadata when available."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 11px;")
        normalization_layout.addWidget(info_label)

        layout.addWidget(normalization_group)

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Apply).clicked.connect(self._apply_settings)

        layout.addWidget(button_box)

    def _load_current_settings(self):
        """Load current settings from music player."""
        try:
            # Load audio devices
            self._load_audio_devices()

            # Load crossfade settings
            self.crossfade_check.setChecked(
                getattr(self.music_player, "crossfade_enabled", False)
            )
            self.crossfade_spinbox.setValue(
                getattr(self.music_player, "crossfade_duration", 3000)
            )

            # Load normalization settings
            self.normalization_check.setChecked(
                getattr(self.music_player, "normalization_enabled", False)
            )
            self.normalization_spinbox.setValue(
                getattr(self.music_player, "normalization_target", -23.0)
            )

            # Load exclusive mode
            self.exclusive_mode_check.setChecked(
                getattr(self.music_player, "exclusive_mode", False)
            )

            # Enable/disable dependent controls
            self._update_controls_state()

        except Exception as e:
            logger.error(f"Error loading audio settings: {e}")

    def _load_audio_devices(self):
        """Load available audio devices into combo box."""
        try:
            self.device_combo.clear()

            # Add default device option
            self.device_combo.addItem("Default Output Device", "")

            # Get available devices from music player
            devices = self.music_player.get_audio_devices()

            current_device = getattr(self.music_player, "current_device", None)
            current_index = 0  # Default to first item

            for i, device in enumerate(devices):
                device_name = device.get("name", f"Device {device['id']}")
                is_default = device.get("default", False)

                display_name = device_name
                if is_default:
                    display_name += " (Default)"

                self.device_combo.addItem(display_name, device["id"])

                # Select current device if it matches
                if current_device and (
                    str(device["id"]) == str(current_device)
                    or device_name == current_device
                ):
                    current_index = i + 1  # +1 because of default device at index 0

            self.device_combo.setCurrentIndex(current_index)

        except Exception as e:
            logger.error(f"Error loading audio devices: {e}")
            self.device_combo.addItem("Error loading devices", "")

    def _update_controls_state(self):
        """Update enabled/disabled state of dependent controls."""
        crossfade_enabled = self.crossfade_check.isChecked()
        normalization_enabled = self.normalization_check.isChecked()

        self.crossfade_spinbox.setEnabled(crossfade_enabled)
        self.normalization_spinbox.setEnabled(normalization_enabled)

    def _on_device_changed(self, text):
        """Handle audio device selection change."""
        if text and text != "Error loading devices":
            device_data = self.device_combo.currentData()
            if device_data is not None:
                self.audio_device_changed.emit(device_data)

    def _on_crossfade_toggled(self, enabled):
        """Handle crossfade toggle."""
        self.crossfade_changed.emit(enabled)
        self._update_controls_state()

    def _on_normalization_toggled(self, enabled):
        """Handle normalization toggle."""
        self.normalization_changed.emit(enabled)
        self._update_controls_state()

    def _apply_settings(self):
        """Apply settings to music player."""
        try:
            # Apply audio device
            device_data = self.device_combo.currentData()
            if device_data is not None:
                self.music_player.set_audio_device(device_data)

            # Apply crossfade settings
            self.music_player.enable_crossfade(self.crossfade_check.isChecked())
            self.music_player.set_crossfade_duration(self.crossfade_spinbox.value())

            # Apply normalization settings
            self.music_player.enable_normalization(self.normalization_check.isChecked())
            self.music_player.set_normalization_target(
                self.normalization_spinbox.value()
            )

            # Apply exclusive mode
            self.music_player.set_exclusive_mode(self.exclusive_mode_check.isChecked())

            logger.info("Audio settings applied successfully")

        except Exception as e:
            logger.error(f"Error applying audio settings: {e}")

    def accept(self):
        """Apply settings and close dialog."""
        self._apply_settings()
        super().accept()

    def get_settings(self) -> dict:
        """Get current settings as dictionary."""
        return {
            "audio_device": self.device_combo.currentData(),
            "crossfade_enabled": self.crossfade_check.isChecked(),
            "crossfade_duration": self.crossfade_spinbox.value(),
            "normalization_enabled": self.normalization_check.isChecked(),
            "normalization_target": self.normalization_spinbox.value(),
            "exclusive_mode": self.exclusive_mode_check.isChecked(),
        }

    def set_settings(self, settings: dict):
        """Set dialog settings from dictionary."""
        try:
            # Audio device
            if "audio_device" in settings:
                device_value = settings["audio_device"]
                for i in range(self.device_combo.count()):
                    if self.device_combo.itemData(i) == device_value:
                        self.device_combo.setCurrentIndex(i)
                        break

            # Crossfade
            if "crossfade_enabled" in settings:
                self.crossfade_check.setChecked(settings["crossfade_enabled"])
            if "crossfade_duration" in settings:
                self.crossfade_spinbox.setValue(settings["crossfade_duration"])

            # Normalization
            if "normalization_enabled" in settings:
                self.normalization_check.setChecked(settings["normalization_enabled"])
            if "normalization_target" in settings:
                self.normalization_spinbox.setValue(settings["normalization_target"])

            # Exclusive mode
            if "exclusive_mode" in settings:
                self.exclusive_mode_check.setChecked(settings["exclusive_mode"])

            self._update_controls_state()

        except Exception as e:
            logger.error(f"Error setting audio dialog settings: {e}")


def show_audio_settings_dialog(music_player, parent=None):
    """
    Convenience function to show audio settings dialog.

    Args:
        music_player: Instance of MusicPlayer
        parent: Parent widget

    Returns:
        bool: True if settings were applied, False if canceled
    """
    dialog = AudioSettingsDialog(music_player, parent)

    # Connect signals to music player for real-time updates
    dialog.audio_device_changed.connect(music_player.set_audio_device)
    dialog.crossfade_changed.connect(music_player.enable_crossfade)
    dialog.crossfade_duration_changed.connect(music_player.set_crossfade_duration)
    dialog.normalization_changed.connect(music_player.enable_normalization)
    dialog.normalization_target_changed.connect(music_player.set_normalization_target)
    dialog.exclusive_mode_changed.connect(music_player.set_exclusive_mode)

    result = dialog.exec()

    # Disconnect signals after dialog closes
    dialog.audio_device_changed.disconnect()
    dialog.crossfade_changed.disconnect()
    dialog.crossfade_duration_changed.disconnect()
    dialog.normalization_changed.disconnect()
    dialog.normalization_target_changed.disconnect()
    dialog.exclusive_mode_changed.disconnect()

    return result == QDialog.Accepted
