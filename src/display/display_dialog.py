from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from src.core.logger_config import logger
from src.display.display_settings import DisplaySettings


class DisplaySettingsDialog(QDialog):
    """
    UI for modifying global display settings.
    This dialog does NOT own the state — it only manipulates DisplaySettings.
    """

    def __init__(self, display: DisplaySettings, parent=None):
        super().__init__(parent)
        self.display = display

        self.setWindowTitle("Display Settings")
        self.setModal(True)
        self.resize(420, 340)

        logger.debug("Opening display settings dialog")
        self._build_ui()
        self._populate()
        self._connect_signals()

    # ---------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # Theme selector
        self.theme_combo = QComboBox()
        form.addRow("Theme:", self.theme_combo)

        # UI scale
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(80, 140)  # percent
        self.scale_label = QLabel()
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addWidget(self.scale_label)
        form.addRow("UI Scale:", scale_layout)

        # Font family
        self.font_combo = QComboBox()
        form.addRow("Font:", self.font_combo)

        # Font size
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(7, 20)
        form.addRow("Font Size:", self.font_size_spin)

        # ---- Menu Bar behaviour ----
        # A small separator label makes the section feel distinct
        separator_label = QLabel("Menu Bar")
        separator_label.setProperty("title", True)
        form.addRow(separator_label)

        self.auto_hide_check = QCheckBox("Auto-hide menu bar (shows on mouse-over)")
        self.auto_hide_check.setToolTip(
            "When enabled, the menu bar will hide automatically. "
            "Move your mouse to the top of the window to reveal it."
        )
        form.addRow("", self.auto_hide_check)

        # ---- Explicit content ----
        content_label = QLabel("Explicit Content")
        content_label.setProperty("title", True)
        form.addRow(content_label)

        self.blur_art_check = QCheckBox("Blur album art marked explicit")
        self.blur_art_check.setToolTip(
            "When enabled, cover/liner art for albums marked as having "
            "explicit art is shown blurred until you choose to reveal it."
        )
        form.addRow("", self.blur_art_check)

        self.censor_words_check = QCheckBox("Censor explicit words")
        self.censor_words_check.setToolTip(
            "When enabled, words listed in assets/explicit_words.txt are "
            "masked with asterisks wherever text is displayed (lyrics, "
            "album titles, track titles, etc.)."
        )
        form.addRow("", self.censor_words_check)

        layout.addLayout(form)

        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        layout.addWidget(self.buttons)

    # ---------------------------------------------------------
    # Populate controls from current DisplaySettings state
    # ---------------------------------------------------------

    def _populate(self):
        # Themes
        themes = sorted(p.stem for p in self.display.theme_dir.glob("*.qss"))
        self.theme_combo.addItems(themes)

        if self.display.theme_name:
            index = self.theme_combo.findText(self.display.theme_name)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)

        # UI scale
        percent = int(self.display.ui_scale * 100)
        self.scale_slider.setValue(percent)
        self.scale_label.setText(f"{percent}%")

        # Fonts
        fonts = QFontDatabase.families()
        self.font_combo.addItems(fonts)

        index = self.font_combo.findText(self.display.font_family)
        if index >= 0:
            self.font_combo.setCurrentIndex(index)

        # Font size
        self.font_size_spin.setValue(self.display.font_size)

        # Menu bar auto-hide — read current value from DisplaySettings
        self.auto_hide_check.setChecked(self.display.get_menu_bar_auto_hide())

        # Explicit content options
        self.blur_art_check.setChecked(self.display.get_blur_explicit_art())
        self.censor_words_check.setChecked(self.display.get_censor_explicit_words())

    # ---------------------------------------------------------
    # Signal wiring
    # ---------------------------------------------------------

    def _connect_signals(self):
        self.theme_combo.currentTextChanged.connect(self.display.set_theme)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.font_combo.currentTextChanged.connect(self.display.set_font_family)
        self.font_size_spin.valueChanged.connect(self.display.set_font_size)

        # Auto-hide: call DisplaySettings which saves to config and emits signal
        self.auto_hide_check.toggled.connect(self.display.set_menu_bar_auto_hide)

        self.blur_art_check.toggled.connect(self.display.set_blur_explicit_art)
        self.censor_words_check.toggled.connect(self.display.set_censor_explicit_words)

        self.buttons.rejected.connect(self.close)

    # ---------------------------------------------------------
    # Slots
    # ---------------------------------------------------------

    def _on_scale_changed(self, value: int):
        scale = value / 100.0
        self.scale_label.setText(f"{value}%")
        self.display.set_ui_scale(scale)
