from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from equalizer_utility import EqualizerUtility


class EqualizerDialog(QDialog):
    """equalizer configuration dialog."""

    def __init__(self, equalizer: EqualizerUtility, Config=None, parent=None):
        super().__init__(parent)
        self.equalizer = equalizer
        self.config = Config
        self.setWindowTitle("Equalizer")
        self.setModal(False)

        # Set sensible size policies
        self.setMinimumSize(900, 500)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.sliders = []
        self.gain_labels = []

        self.init_ui()
        self.load_current_settings()

        # Connect equalizer signals
        self.equalizer.equalizer_changed.connect(self.on_equalizer_changed)

        # Adjust size after UI is built
        self.adjustSize()

    def init_ui(self):
        """Initialize the user interface."""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Enable/disable checkbox and presets
        control_layout = QHBoxLayout()

        self.enable_checkbox = QCheckBox("Enable Equalizer")
        self.enable_checkbox.setChecked(self.equalizer.is_enabled())
        self.enable_checkbox.toggled.connect(self.equalizer.set_enabled)
        control_layout.addWidget(self.enable_checkbox)

        control_layout.addStretch()

        # Presets combo box
        control_layout.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(120)
        self.preset_combo.addItems(["Custom"] + list(self.equalizer.presets.keys()))
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        control_layout.addWidget(self.preset_combo)

        layout.addLayout(control_layout)

        # Equalizer bands group - use scroll area for many bands
        self.bands_group = QGroupBox("Equalizer Bands (1/3 Octave)")
        self.bands_layout = QVBoxLayout(self.bands_group)

        # Create scroll area for bands
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMinimumHeight(350)

        # Container for sliders
        slider_widget = QWidget()
        self.slider_layout = QGridLayout(slider_widget)
        self.slider_layout.setSpacing(5)
        self.slider_layout.setContentsMargins(10, 10, 10, 10)

        self.create_band_sliders()

        scroll_area.setWidget(slider_widget)
        layout.addWidget(self.bands_group)

        # Control buttons

        button_layout = QHBoxLayout()

        self.reset_button = QPushButton("Reset to Flat")
        self.reset_button.clicked.connect(self.equalizer.reset)
        button_layout.addWidget(self.reset_button)

        # Add save/load buttons if config is available
        if self.config:
            self.save_button = QPushButton("Save as Custom")
            self.save_button.clicked.connect(self.save_custom_preset)
            button_layout.addWidget(self.save_button)

            self.load_button = QPushButton("Load from Config")
            self.load_button.clicked.connect(self.load_from_config)
            button_layout.addWidget(self.load_button)

        button_layout.addStretch()

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

    def save_custom_preset(self):
        """Save current EQ settings as a custom preset."""
        if not self.config:
            QMessageBox.warning(self, "Error", "Configuration not available")
            return

        # Get preset name from user
        preset_name, ok = QInputDialog.getText(
            self,
            "Save Custom EQ Preset",
            "Enter preset name:",
            text=self.config.get_equalizer_custom_preset_name(),
        )

        if ok and preset_name:
            # Save to config
            band_gains = self.equalizer.get_band_gains()
            self.config.save_equalizer_settings(
                self.equalizer.is_enabled(), band_gains, preset_name
            )

            # Update preset combo if this is a new named preset
            if preset_name != "Custom" and preset_name not in self.equalizer.presets:
                self.equalizer.presets[preset_name] = band_gains
                self.preset_combo.addItem(preset_name)
                self.preset_combo.setCurrentText(preset_name)

            QMessageBox.information(self, "Success", f"EQ preset '{preset_name}' saved")

    # Add the load_from_config method:
    def load_from_config(self):
        """Load EQ settings from configuration."""
        if not self.config:
            QMessageBox.warning(self, "Error", "Configuration not available")
            return

        reply = QMessageBox.question(
            self,
            "Load EQ Settings",
            "Load equalizer settings from configuration? This will overwrite current settings.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.equalizer.load_from_config(self.config)
            self.load_current_settings()

            # Update preset combo to show Custom
            self.preset_combo.setCurrentText("Custom")

    def create_band_sliders(self):
        """Create a horizontal row of vertical sliders for each band (no scrolling)."""
        # Clear previous sliders and labels
        self.sliders.clear()
        self.gain_labels.clear()

        # Create container widget for horizontal layout
        self.slider_container = QWidget()
        h_layout = QHBoxLayout(self.slider_container)
        h_layout.setSpacing(15)
        h_layout.setContentsMargins(10, 10, 10, 10)
        h_layout.setAlignment(Qt.AlignHCenter)

        # Add sliders for each band
        for band_idx, band in enumerate(self.equalizer.bands):
            vbox = QVBoxLayout()
            vbox.setAlignment(Qt.AlignHCenter)

            # Gain label (top)
            gain_label = QLabel(f"{band['gain']:+.1f} dB")
            gain_label.setStyleSheet("font-size: 8px;")
            gain_label.setAlignment(Qt.AlignCenter)
            gain_label.setFixedHeight(20)
            self.gain_labels.append(gain_label)
            vbox.addWidget(gain_label)

            # Slider
            slider = QSlider(Qt.Vertical)
            slider.setRange(-120, 120)  # -12 dB to +12 dB in 0.1 dB steps
            slider.setValue(int(band["gain"] * 10))
            slider.setTickPosition(QSlider.TicksBothSides)
            slider.setTickInterval(60)  # 6 dB intervals
            slider.setMinimumHeight(120)
            slider.valueChanged.connect(
                lambda value, idx=band_idx: self.on_slider_changed(idx, value)
            )
            self.sliders.append(slider)
            vbox.addWidget(slider)

            # Frequency label (bottom)
            freq_label = QLabel(f"{band['freq']} Hz")
            freq_label.setStyleSheet("font-size: 8px;")
            freq_label.setAlignment(Qt.AlignCenter)
            freq_label.setFixedHeight(20)
            vbox.addWidget(freq_label)

            # Band name label (optional, below frequency)
            name_label = QLabel(band["label"])
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setStyleSheet("font-size: 7px;")
            name_label.setFixedHeight(15)
            name_label.setStyleSheet("font-size: 9px;")
            vbox.addWidget(name_label)

            # Add this vertical layout to the horizontal container
            h_layout.addLayout(vbox)

        # Clear previous widgets in the group box layout
        while self.bands_layout.count():
            item = self.bands_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Add the horizontal slider container to the group box layout
        self.bands_layout.addWidget(self.slider_container)

    def on_slider_changed(self, band_index: int, value: int):
        """Handle slider value changes."""
        gain = value / 10.0  # Convert to dB
        self.equalizer.set_band_gain(band_index, gain)
        self.gain_labels[band_index].setText(f"{gain:+.1f} dB")
        self.preset_combo.setCurrentText("Custom")
        self.last_was_custom = True

    def on_preset_changed(self, preset_name: str):
        """Handle preset selection changes."""
        if preset_name != "Custom":
            self.equalizer.set_preset(preset_name)
            self.load_current_settings()

            # Auto-save custom settings if we had a custom configuration
            if (
                self.config
                and hasattr(self, "last_was_custom")
                and self.last_was_custom
            ):
                # Get the custom preset name from config
                custom_name = self.config.get_equalizer_custom_preset_name()
                band_gains = self.equalizer.get_band_gains()
                self.config.save_equalizer_settings(
                    self.equalizer.is_enabled(), band_gains, custom_name
                )

        self.last_was_custom = preset_name == "Custom"

    def on_equalizer_changed(self, settings: Dict):
        """Update UI when equalizer settings change externally."""
        self.load_current_settings()

    def load_current_settings(self):
        """Load current equalizer settings into UI."""
        settings = self.equalizer.get_settings()

        # Update enable checkbox
        self.enable_checkbox.blockSignals(True)
        self.enable_checkbox.setChecked(settings["enabled"])
        self.enable_checkbox.blockSignals(False)

        # Update sliders and labels
        for i, band in enumerate(settings["bands"]):
            if i < len(self.sliders):
                self.sliders[i].blockSignals(True)
                self.sliders[i].setValue(int(band["gain"] * 10))
                self.sliders[i].blockSignals(False)
                self.gain_labels[i].setText(f"{band['gain']:+.1f} dB")

    def showEvent(self, event):
        """Handle dialog show event."""
        super().showEvent(event)
        self.load_current_settings()
        # Ensure proper sizing
        self.adjustSize()

    def accept(self):
        """Handle dialog acceptance with auto-save option."""
        if self.config:
            # Auto-save current settings to config
            band_gains = self.equalizer.get_band_gains()
            preset_name = "Custom"

            # If we're on a named preset, use that name
            current_preset = self.preset_combo.currentText()
            if current_preset != "Custom":
                preset_name = current_preset

            self.config.save_equalizer_settings(
                self.equalizer.is_enabled(), band_gains, preset_name
            )

        super().accept()
