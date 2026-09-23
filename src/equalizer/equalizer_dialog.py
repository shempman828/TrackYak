import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from src.equalizer.equalizer_utility import EqualizerUtility
from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

_AXIS_MARGIN_LEFT = 34
_AXIS_MARGIN_RIGHT = 10
_DB_RANGE = 12.0
_FREQ_MIN = 32
_FREQ_MAX = 16000


class FrequencyResponseCurve(QWidget):
    """Live-updating plot of the equalizer's combined filter response."""

    def __init__(self, equalizer: EqualizerUtility, parent=None):
        super().__init__(parent)
        self.equalizer = equalizer
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        plot_rect = QRectF(
            _AXIS_MARGIN_LEFT,
            6,
            rect.width() - _AXIS_MARGIN_LEFT - _AXIS_MARGIN_RIGHT,
            rect.height() - 12,
        )

        active = self.equalizer.is_enabled()
        line_color = QColor("#8599ea") if active else QColor("#555e7a")
        fill_color = QColor(line_color)
        fill_color.setAlpha(70 if active else 35)

        for db in (-12, -6, 0, 6, 12):
            y = self._y_for_db(db, plot_rect)
            is_zero = db == 0
            pen = QPen(QColor(133, 153, 234, 90 if is_zero else 30))
            pen.setWidthF(1.2 if is_zero else 1.0)
            painter.setPen(pen)
            painter.drawLine(QPointF(plot_rect.left(), y), QPointF(plot_rect.right(), y))
            if db in (12, 0, -12):
                painter.setPen(QColor("#555e7a"))
                font = painter.font()
                font.setPointSizeF(7.5)
                painter.setFont(font)
                painter.drawText(
                    QRectF(0, y - 7, _AXIS_MARGIN_LEFT - 6, 14),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{db:+d}" if db else "0",
                )

        for band in self.equalizer.bands:
            x = self._x_for_freq(band["freq"], plot_rect)
            painter.setPen(QColor(133, 153, 234, 22))
            painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))

        freqs, gains_db = self.equalizer.get_frequency_response()
        path = QPainterPath()
        fill_path = QPainterPath()
        zero_y = self._y_for_db(0, plot_rect)
        for i, (freq, gain) in enumerate(zip(freqs, gains_db, strict=True)):
            x = self._x_for_freq(freq, plot_rect)
            y = self._y_for_db(gain, plot_rect)
            if i == 0:
                path.moveTo(x, y)
                fill_path.moveTo(x, zero_y)
                fill_path.lineTo(x, y)
            else:
                path.lineTo(x, y)
                fill_path.lineTo(x, y)
        fill_path.lineTo(plot_rect.right(), zero_y)
        fill_path.closeSubpath()

        painter.fillPath(fill_path, fill_color)
        pen = QPen(line_color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawPath(path)

    @staticmethod
    def _x_for_freq(freq: float, plot_rect: QRectF) -> float:
        lo, hi = math.log10(_FREQ_MIN), math.log10(_FREQ_MAX)
        frac = (math.log10(freq) - lo) / (hi - lo)
        return plot_rect.left() + frac * plot_rect.width()

    @staticmethod
    def _y_for_db(db: float, plot_rect: QRectF) -> float:
        frac = (db + _DB_RANGE) / (2 * _DB_RANGE)
        return plot_rect.bottom() - frac * plot_rect.height()


class _ZeroLineRow(QWidget):
    """Slider row container that paints a shared 0 dB reference line behind the sliders."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(133, 153, 234, 45), 1))
        y = self.height() / 2
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        super().paintEvent(event)


class EqualizerDialog(QDialog):
    """equalizer configuration dialog."""

    def __init__(self, equalizer: EqualizerUtility, Config=None, parent=None):
        super().__init__(parent)
        self.equalizer = equalizer
        self.config = Config
        self.setWindowTitle("Equalizer")
        self.setModal(False)

        # Set sensible size policies
        self.setMinimumSize(900, 620)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.sliders = []

        self.init_ui()
        self.load_current_settings()

        # Connect equalizer signals
        self.equalizer.equalizer_changed.connect(self.on_equalizer_changed)

        # Adjust size after UI is built
        self.adjustSize()

    def init_ui(self):
        """Initialize the user interface."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        # Enable toggle and presets
        control_layout = QHBoxLayout()

        self.enable_button = QPushButton("Enabled")
        self.enable_button.setObjectName("EqEnableToggle")
        self.enable_button.setCheckable(True)
        self.enable_button.setChecked(self.equalizer.is_enabled())
        self.enable_button.toggled.connect(self.equalizer.set_enabled)
        control_layout.addWidget(self.enable_button)

        control_layout.addStretch()

        control_layout.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(140)
        self.preset_combo.addItems(["Custom", *list(self.equalizer.presets.keys())])
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        control_layout.addWidget(self.preset_combo)

        layout.addLayout(control_layout)

        # Frequency response graph + band sliders, sharing one themed panel
        self.bands_group = QGroupBox("Frequency Response")
        bands_layout = QVBoxLayout(self.bands_group)
        bands_layout.setSpacing(8)

        self.response_curve = FrequencyResponseCurve(self.equalizer)
        bands_layout.addWidget(self.response_curve)

        self.create_band_sliders()
        bands_layout.addWidget(self.slider_container)
        bands_layout.addWidget(self.freq_label_row)

        layout.addWidget(self.bands_group, stretch=1)

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
        self.close_button.setObjectName("PrimaryButton")
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

    def save_custom_preset(self):
        """Save current EQ settings as a custom preset."""
        if not self.config:
            logger.warning("Cannot save custom EQ preset: no configuration available")
            show_status_message(self, "Configuration not available")
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

            logger.info(f"Saved custom EQ preset: {preset_name}")
            show_status_message(self, f"EQ preset '{preset_name}' saved")

    # Add the load_from_config method:
    def load_from_config(self):
        """Load EQ settings from configuration."""
        if not self.config:
            logger.warning("Cannot load EQ settings: no configuration available")
            show_status_message(self, "Configuration not available")
            return

        reply = QMessageBox.question(
            self,
            "Load EQ Settings",
            "Load equalizer settings from configuration? This will overwrite current settings.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            logger.info("Loaded equalizer settings from configuration")
            self.equalizer.load_from_config(self.config)
            self.load_current_settings()

            # Update preset combo to show Custom
            self.preset_combo.setCurrentText("Custom")

    def create_band_sliders(self):
        """Build one vertical slider per band, aligned under the response curve."""
        self.sliders.clear()

        self.slider_container = _ZeroLineRow()
        h_layout = QHBoxLayout(self.slider_container)
        h_layout.setSpacing(4)
        h_layout.setContentsMargins(_AXIS_MARGIN_LEFT, 4, _AXIS_MARGIN_RIGHT, 4)

        self.freq_label_row = QWidget()
        freq_layout = QHBoxLayout(self.freq_label_row)
        freq_layout.setSpacing(4)
        freq_layout.setContentsMargins(_AXIS_MARGIN_LEFT, 0, _AXIS_MARGIN_RIGHT, 0)

        for band_idx, band in enumerate(self.equalizer.bands):
            slider = QSlider(Qt.Vertical)
            slider.setRange(-120, 120)  # -12 dB to +12 dB in 0.1 dB steps
            slider.setValue(int(band["gain"] * 10))
            slider.setTickPosition(QSlider.TicksBothSides)
            slider.setTickInterval(60)  # 6 dB intervals
            slider.setMinimumHeight(160)
            slider.setToolTip(self._band_tooltip(band))
            slider.valueChanged.connect(
                lambda value, idx=band_idx: self.on_slider_changed(idx, value)
            )
            self.sliders.append(slider)
            h_layout.addWidget(slider, stretch=1, alignment=Qt.AlignHCenter)

            freq_label = QLabel(self._format_freq(band["freq"]))
            freq_label.setAlignment(Qt.AlignCenter)
            apply_scaled_style(freq_label, "font-size: 10px; color: #7a82a8;")
            freq_layout.addWidget(freq_label, stretch=1)

    @staticmethod
    def _format_freq(freq: int) -> str:
        if freq >= 1000:
            return f"{freq / 1000:g}k"
        return str(freq)

    @staticmethod
    def _band_tooltip(band: dict) -> str:
        return f"{band['label']} ({band['freq']} Hz): {band['gain']:+.1f} dB"

    def on_slider_changed(self, band_index: int, value: int):
        """Handle slider value changes."""
        gain = value / 10.0  # Convert to dB
        self.equalizer.set_band_gain(band_index, gain)
        self.sliders[band_index].setToolTip(self._band_tooltip(self.equalizer.bands[band_index]))
        self.preset_combo.setCurrentText("Custom")
        self.last_was_custom = True

    def on_preset_changed(self, preset_name: str):
        """Handle preset selection changes."""
        if preset_name != "Custom":
            self.equalizer.set_preset(preset_name)
            self.load_current_settings()

            # Auto-save custom settings if we had a custom configuration
            if self.config and hasattr(self, "last_was_custom") and self.last_was_custom:
                # Get the custom preset name from config
                custom_name = self.config.get_equalizer_custom_preset_name()
                band_gains = self.equalizer.get_band_gains()
                self.config.save_equalizer_settings(
                    self.equalizer.is_enabled(), band_gains, custom_name
                )

        self.last_was_custom = preset_name == "Custom"

    def on_equalizer_changed(self, settings: dict):
        """Update UI when equalizer settings change externally."""
        self.load_current_settings()

    def load_current_settings(self):
        """Load current equalizer settings into UI."""
        settings = self.equalizer.get_settings()

        # Update enable toggle
        self.enable_button.blockSignals(True)
        self.enable_button.setChecked(settings["enabled"])
        self.enable_button.blockSignals(False)

        # Update sliders and tooltips
        for i, band in enumerate(settings["bands"]):
            if i < len(self.sliders):
                self.sliders[i].blockSignals(True)
                self.sliders[i].setValue(int(band["gain"] * 10))
                self.sliders[i].blockSignals(False)
                self.sliders[i].setToolTip(self._band_tooltip(band))

        self.response_curve.update()

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
