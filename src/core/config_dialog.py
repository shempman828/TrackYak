"""General Settings dialog: library, playback, appearance, audio and logging options."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
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

from src.core.font_family_worker import FontFamilyWorker
from src.foundation.config_setup import Config
from src.foundation.display_settings import DisplaySettings
from src.foundation.logger_config import logger, reconfigure_logging
from src.foundation.status_utility import show_status_message


class ConfigDialog(QDialog):
    """Tabbed General Settings dialog; Appearance previews live, the rest applies on Apply/OK."""

    # Set by the first FontFamilyWorker to finish; later dialog opens reuse
    # it instead of re-running fc-list (see _create_appearance_tab).
    _canonical_font_families_cache: set | None = None

    def __init__(self, config: Config, display_settings: DisplaySettings | None = None, music_player=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.display_settings = display_settings
        self.music_player = music_player
        self.setWindowTitle("General Settings")
        self.setModal(True)
        self.setMinimumSize(600, 560)

        # Re-scaling the theme's QSS across a full widget tree is too slow
        # to run on every single slider tick during a drag (measured ~18ms+
        # per call against a realistic widget count) — coalesce rapid ticks
        # so dragging stays smooth while still previewing live.
        self._pending_scale_value: int | None = None
        self._scale_debounce_timer = QTimer(self)
        self._scale_debounce_timer.setSingleShot(True)
        self._scale_debounce_timer.setInterval(50)
        self._scale_debounce_timer.timeout.connect(self._preview_pending_scale)
        # The scale preview only restyles what's on screen (see _visible_restyle_roots):
        # OK commits the real, full-app change; Cancel/close undoes every live Appearance preview.
        self.accepted.connect(self._commit_scale_if_pending)
        self.rejected.connect(self._revert_live_appearance)
        self.finished.connect(self._cleanup_font_worker)

        self._setup_ui()
        self._load_current_settings()
        self._snapshot_live_appearance()

    def _show_error(self, message):
        """Show a modal error message."""
        QMessageBox.critical(self, "Error", message)

    def _setup_ui(self):
        """Build the tabs and the Apply/OK/Cancel button row."""
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        self.library_tab = self._create_library_tab()
        self.tabs.addTab(self.library_tab, "Library")

        self.playback_tab = self._create_playback_tab()
        self.tabs.addTab(self.playback_tab, "Playback")

        self.appearance_tab = self._create_appearance_tab()
        self.tabs.addTab(self.appearance_tab, "Appearance")

        self.audio_tab = self._create_audio_tab()
        self.tabs.addTab(self.audio_tab, "Audio")

        self.logging_tab = self._create_logging_tab()
        self.tabs.addTab(self.logging_tab, "Logging")

        layout.addWidget(self.tabs)

        button_layout = QHBoxLayout()

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply_clicked)

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
        """Build the Library tab (music directory)."""
        widget = QWidget()
        layout = QFormLayout(widget)

        dir_layout = QHBoxLayout()
        self.dir_display = QLabel()
        self.dir_display.setMinimumWidth(300)
        self.dir_display.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_directory)

        dir_layout.addWidget(self.dir_display)
        dir_layout.addWidget(self.browse_btn)
        layout.addRow("Music Library:", dir_layout)

        return widget

    def _create_playback_tab(self):
        """Build the Playback tab (default volume, queue persistence)."""
        widget = QWidget()
        layout = QFormLayout(widget)

        volume_layout = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_label = QLabel()
        self.volume_slider.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_label)
        layout.addRow("Default Volume:", volume_layout)

        self.queue_persist_check = QCheckBox("Remember queue between sessions")
        layout.addRow("Queue:", self.queue_persist_check)

        return widget

    def _create_appearance_tab(self):
        """Build the Appearance tab (theme, scale, font, menu bar, explicit content)."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Theme selection — live-applies via DisplaySettings when available,
        # and keeps the startup theme_file config key in sync either way.
        self.theme_combo = QComboBox()
        available_themes = self.display_settings.get_available_themes() if self.display_settings is not None else [Path(name).stem for name in self.config.get_available_themes()]
        self.theme_combo.addItems(available_themes if available_themes else ["default"])
        if self.display_settings is not None:
            self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addRow("Theme:", self.theme_combo)

        # UI scale (percent)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(80, 140)
        self.scale_label = QLabel()
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addWidget(self.scale_label)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        layout.addRow("UI Scale:", scale_layout)

        # QFontDatabase.families() lists every fontconfig named instance of a variable font
        # (Noto Sans alone adds ~900); those aliases are filtered out, and the Script combo
        # narrows the rest by writing system.
        self.font_script_combo = QComboBox()
        self.font_script_combo.addItem("Any", QFontDatabase.WritingSystem.Any)
        for ws in QFontDatabase.writingSystems():
            self.font_script_combo.addItem(QFontDatabase.writingSystemName(ws), ws)
        self.font_script_combo.currentIndexChanged.connect(lambda _: self._populate_font_combo())

        self.font_combo = QComboBox()
        if ConfigDialog._canonical_font_families_cache is not None:
            self._canonical_font_families = ConfigDialog._canonical_font_families_cache
            self._populate_font_combo()
        else:
            # fc-list clustering is slow on the first open of a session: show a
            # placeholder and fill in when the background worker lands.
            self._canonical_font_families = set()
            self.font_combo.addItem("Loading fonts…")
            self.font_combo.setEnabled(False)
            self.font_script_combo.setEnabled(False)
            self._font_family_worker = FontFamilyWorker(parent=self)
            self._font_family_worker.computed.connect(self._on_font_families_computed)
            self._font_family_worker.start()
        if self.display_settings is not None:
            self.font_combo.currentTextChanged.connect(self.display_settings.set_font_family)

        font_layout = QHBoxLayout()
        font_layout.addWidget(self.font_combo, 2)
        font_layout.addWidget(QLabel("Script:"))
        font_layout.addWidget(self.font_script_combo, 1)
        layout.addRow("Font:", font_layout)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(7, 20)
        if self.display_settings is not None:
            self.font_size_spin.valueChanged.connect(self.display_settings.set_font_size)
        layout.addRow("Font Size:", self.font_size_spin)

        # ---- Menu Bar behaviour ----
        menu_bar_label = QLabel("Menu Bar")
        menu_bar_label.setProperty("title", True)
        layout.addRow(menu_bar_label)

        self.auto_hide_check = QCheckBox("Auto-hide menu bar (shows on mouse-over)")
        self.auto_hide_check.setToolTip("When enabled, the menu bar will hide automatically. Move your mouse to the top of the window to reveal it.")
        if self.display_settings is not None:
            self.auto_hide_check.toggled.connect(self.display_settings.set_menu_bar_auto_hide)
        layout.addRow("", self.auto_hide_check)

        # ---- Explicit content ----
        content_label = QLabel("Explicit Content")
        content_label.setProperty("title", True)
        layout.addRow(content_label)

        self.blur_art_check = QCheckBox("Blur album art marked explicit")
        self.blur_art_check.setToolTip("When enabled, cover/liner art for albums marked as having explicit art is shown blurred until you choose to reveal it.")
        if self.display_settings is not None:
            self.blur_art_check.toggled.connect(self.display_settings.set_blur_explicit_art)
        layout.addRow("", self.blur_art_check)

        self.censor_words_check = QCheckBox("Censor explicit words")
        self.censor_words_check.setToolTip("When enabled, words listed in assets/explicit_words.txt are masked with asterisks wherever text is displayed (lyrics, album titles, track titles, etc.).")
        if self.display_settings is not None:
            self.censor_words_check.toggled.connect(self.display_settings.set_censor_explicit_words)
        layout.addRow("", self.censor_words_check)

        return widget

    def _create_audio_tab(self):
        """Build the Audio tab (output device, exclusive mode, normalization, waveform)."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Audio output device — only meaningful with a live player
        self.device_combo = None
        if self.music_player is not None:
            self.device_combo = QComboBox()
            self._load_audio_devices()
            layout.addRow("Output Device:", self.device_combo)

        # ---- Bit-Perfect Playback ----
        self.exclusive_mode_check = QCheckBox("Bit-Perfect / Exclusive Mode")
        layout.addRow("", self.exclusive_mode_check)

        exclusive_info_label = QLabel(
            "Opens the output device directly, with no volume/format processing "
            "in between. Only works with a direct hardware device — pick one "
            'labeled "(Direct)" above, not "(Shared)". If the device is busy '
            "(e.g. held by PulseAudio/PipeWire), playback falls back to the "
            "default output."
        )
        exclusive_info_label.setWordWrap(True)
        exclusive_info_label.setProperty("textRole", "muted")
        layout.addRow(exclusive_info_label)

        # ---- Loudness Normalization ----
        normalization_label = QLabel("Loudness Normalization")
        normalization_label.setProperty("title", True)
        layout.addRow(normalization_label)

        self.normalization_check = QCheckBox("Enable Loudness Normalization")
        layout.addRow("", self.normalization_check)

        self.normalization_spin = QDoubleSpinBox()
        self.normalization_spin.setRange(-50.0, -5.0)  # Reasonable LUFS range
        self.normalization_spin.setSingleStep(0.5)
        self.normalization_spin.setSuffix(" LUFS")
        self.normalization_check.toggled.connect(self.normalization_spin.setEnabled)
        layout.addRow("Target Loudness:", self.normalization_spin)

        if self.music_player is None:
            # Normalization is applied through the live player only; without one these controls do nothing.
            for control in (self.normalization_check, self.normalization_spin):
                control.setEnabled(False)
                control.setToolTip("Needs an active player.")

        info_label = QLabel("Normalization adjusts track volumes to a consistent loudness level. Uses ReplayGain metadata when available.")
        info_label.setWordWrap(True)
        info_label.setProperty("textRole", "muted")
        layout.addRow(info_label)

        # ---- Waveform Display ----
        waveform_label = QLabel("Waveform Display")
        waveform_label.setProperty("title", True)
        layout.addRow(waveform_label)

        self.waveform_log_scale_check = QCheckBox("Perceptual (Log) Waveform Display")
        layout.addRow("", self.waveform_log_scale_check)

        waveform_info_label = QLabel(
            "Shows more visual variation for already-loud masters, at the cost of "
            "no longer being a literally accurate amplitude picture. The cached "
            "peak data itself is unaffected — this only changes how it's drawn."
        )
        waveform_info_label.setWordWrap(True)
        waveform_info_label.setProperty("textRole", "muted")
        layout.addRow(waveform_info_label)

        return widget

    def _create_logging_tab(self):
        """Build the Logging tab (level, destinations, file rotation)."""
        widget = QWidget()
        layout = QFormLayout(widget)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_combo.setToolTip("DEBUG shows the most detail. INFO is recommended for normal use. WARNING and above only show problems.")
        layout.addRow("Log Level:", self.log_level_combo)

        self.console_logging_check = QCheckBox("Enable console logging")
        layout.addRow("", self.console_logging_check)

        self.file_logging_check = QCheckBox("Enable file logging")
        layout.addRow("", self.file_logging_check)

        self.max_file_size_spin = QSpinBox()
        self.max_file_size_spin.setRange(1, 100)
        self.max_file_size_spin.setSuffix(" MB")
        layout.addRow("Max Log File Size:", self.max_file_size_spin)

        self.backup_count_spin = QSpinBox()
        self.backup_count_spin.setRange(1, 50)
        self.backup_count_spin.setToolTip("How many old log files to keep before deleting the oldest.")
        layout.addRow("Log Backup Files:", self.backup_count_spin)

        return widget

    # ------------------------------------------------------------------
    # Appearance tab live-preview slots (only wired when display_settings
    # is available — see _create_appearance_tab)
    # ------------------------------------------------------------------

    def _live_appearance_controls(self) -> list[QWidget]:
        """Return the Appearance controls whose signals live-apply through DisplaySettings."""
        return [self.theme_combo, self.scale_slider, self.font_combo, self.font_size_spin, self.auto_hide_check, self.blur_art_check, self.censor_words_check]

    def _configured_font_family(self) -> str:
        """Return the font family currently in effect."""
        return self.display_settings.font_family if self.display_settings is not None else self.config.get_font_family()

    def _on_font_families_computed(self, families: set):
        """Replace the "Loading fonts…" placeholder with the computed family list."""
        ConfigDialog._canonical_font_families_cache = families
        self._canonical_font_families = families
        self.font_combo.setEnabled(True)
        self.font_script_combo.setEnabled(True)

        # Fill and select silently: the placeholder fallback to the first font must never live-apply.
        self.font_combo.blockSignals(True)
        try:
            self._populate_font_combo()
            index = self.font_combo.findText(self._configured_font_family())
            if index >= 0:
                self.font_combo.setCurrentIndex(index)
        finally:
            self.font_combo.blockSignals(False)

    def _cleanup_font_worker(self):
        """Cancel and join FontFamilyWorker so Qt never destroys a running QThread."""
        worker = getattr(self, "_font_family_worker", None)
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            worker.wait()

    def _populate_font_combo(self):
        """Refill font_combo for the current Script filter, keeping the selection or falling back to the first entry."""
        # The first-entry fallback live-applies through currentTextChanged, like any other choice.
        writing_system = self.font_script_combo.currentData()
        families = sorted(f for f in QFontDatabase.families(writing_system) if f in self._canonical_font_families)

        previous = self.font_combo.currentText()
        was_blocked = self.font_combo.blockSignals(True)
        self.font_combo.clear()
        self.font_combo.addItems(families)
        self.font_combo.blockSignals(was_blocked)

        index = self.font_combo.findText(previous)
        if index < 0 and families:
            index = 0
        if index >= 0:
            self.font_combo.setCurrentIndex(index)

    def _on_theme_changed(self, name: str):
        """Live-apply the selected theme and keep the startup theme_file key in sync."""
        self.display_settings.set_theme(name)
        self.config.set_theme_file(f"{name}.qss")
        self.config.save()

    def _on_scale_changed(self, value: int):
        """Update the scale label and queue a debounced live preview."""
        self.scale_label.setText(f"{value}%")
        if self.display_settings is not None:
            self._pending_scale_value = value
            self._scale_debounce_timer.start()

    def _visible_restyle_roots(self) -> list[QWidget]:
        """Return this dialog plus the widgets visible behind it, the only ones a scale preview restyles."""
        roots: list[QWidget] = [self]
        main_window = self.parent()
        if main_window is None:
            return roots

        stacked = getattr(main_window, "stacked_widget", None)
        if stacked is not None:
            current = stacked.currentWidget()
            if current is not None:
                roots.append(current)

        if hasattr(main_window, "menuBar"):
            menu_bar = main_window.menuBar()
            if menu_bar is not None:
                roots.append(menu_bar)
        # Never call statusBar(): the main window removed its status bar, and that call would create a new empty one.
        status_widget = getattr(main_window, "status_bar_widget", None)
        if status_widget is not None:
            roots.append(status_widget)
        if hasattr(main_window, "findChildren"):
            roots.extend(main_window.findChildren(QDockWidget))

        return roots

    def _preview_pending_scale(self):
        """Preview the pending scale on the visible widgets only."""
        if self._pending_scale_value is not None and self.display_settings is not None:
            self.display_settings.preview_ui_scale_in(self._pending_scale_value / 100.0, self._visible_restyle_roots())

    def _commit_scale_if_pending(self, *_args):
        """Commit a previewed scale app-wide and persist it."""
        self._scale_debounce_timer.stop()
        if self._pending_scale_value is not None and self.display_settings is not None:
            self.display_settings.set_ui_scale(self._pending_scale_value / 100.0)
        self._pending_scale_value = None

    def _snapshot_live_appearance(self):
        """Record the live-applied Appearance values so Cancel can restore them."""
        ds = self.display_settings
        if ds is None:
            self._appearance_snapshot = None
            return
        self._appearance_snapshot = {
            "theme_name": ds.theme_name,
            "theme_file": self.config.get_theme_file(),
            "ui_scale": ds.ui_scale,
            "font_family": ds.font_family,
            "font_size": ds.font_size,
            "menu_bar_auto_hide": ds.get_menu_bar_auto_hide(),
            "blur_explicit_art": ds.get_blur_explicit_art(),
            "censor_explicit_words": ds.get_censor_explicit_words(),
        }

    def _revert_live_appearance(self, *_args):
        """Undo every live Appearance change made since the dialog opened or Apply was last pressed."""
        self._scale_debounce_timer.stop()
        ds = self.display_settings
        snap = getattr(self, "_appearance_snapshot", None)
        if ds is None or snap is None:
            self._pending_scale_value = None
            return

        if self._pending_scale_value is not None:
            # The preview touched only the visible roots and never the app-wide sheet; restyle those roots back.
            ds.preview_ui_scale_in(snap["ui_scale"], self._visible_restyle_roots())
            self._pending_scale_value = None

        if snap["theme_name"] and ds.theme_name != snap["theme_name"]:
            ds.set_theme(snap["theme_name"])
        if self.config.get_theme_file() != snap["theme_file"]:
            self.config.set_theme_file(snap["theme_file"])
            self.config.save()
        if ds.font_family != snap["font_family"]:
            ds.set_font_family(snap["font_family"])
        if ds.font_size != snap["font_size"]:
            ds.set_font_size(snap["font_size"])
        if ds.get_menu_bar_auto_hide() != snap["menu_bar_auto_hide"]:
            ds.set_menu_bar_auto_hide(snap["menu_bar_auto_hide"])
        if ds.get_blur_explicit_art() != snap["blur_explicit_art"]:
            ds.set_blur_explicit_art(snap["blur_explicit_art"])
        if ds.get_censor_explicit_words() != snap["censor_explicit_words"]:
            ds.set_censor_explicit_words(snap["censor_explicit_words"])

    # ------------------------------------------------------------------
    # Audio tab helpers
    # ------------------------------------------------------------------

    def _load_audio_devices(self):
        """Load available audio devices into combo box, selecting the saved device."""
        try:
            self.device_combo.clear()
            self.device_combo.addItem("Default Output Device", "")

            devices = self.music_player.get_audio_devices()

            saved_device = self.config.get_output_device()
            current_device = saved_device if saved_device != "default" else getattr(self.music_player, "current_device", None)

            current_index = 0

            for i, device in enumerate(devices):
                device_name = device.get("name", f"Device {device['id']}")
                is_default = device.get("default", False)

                display_name = device_name
                display_name += " (Direct)" if device.get("direct") else " (Shared)"
                if is_default:
                    display_name += " (Default)"

                self.device_combo.addItem(display_name, device["id"])

                if current_device and (str(device["id"]) == str(current_device) or device_name == current_device):
                    current_index = i + 1

            self.device_combo.setCurrentIndex(current_index)

        except (KeyError, RuntimeError) as e:
            logger.error(f"Error loading audio devices: {e}")
            self.device_combo.addItem("Error loading devices", "")

    # ------------------------------------------------------------------
    # Load / Apply
    # ------------------------------------------------------------------

    def _load_current_settings(self):
        """Load current settings from config into UI controls."""
        # Loading must not fire the live-apply slots (theme re-apply, config save, queued scale commit).
        live_controls = self._live_appearance_controls()
        for control in live_controls:
            control.blockSignals(True)
        try:
            self.dir_display.setText(str(self.config.get_base_directory()))

            volume = self.config.get_volume()
            self.volume_slider.setValue(volume)
            self.volume_label.setText(f"{volume}%")

            self.queue_persist_check.setChecked(self.config.get_persist_queue())

            # Appearance settings
            if self.display_settings is not None:
                if self.display_settings.theme_name:
                    index = self.theme_combo.findText(self.display_settings.theme_name)
                    if index >= 0:
                        self.theme_combo.setCurrentIndex(index)
            else:
                index = self.theme_combo.findText(Path(self.config.get_theme_file()).stem)
                if index >= 0:
                    self.theme_combo.setCurrentIndex(index)

            ui_scale = self.display_settings.ui_scale if self.display_settings is not None else self.config.get_ui_scale()
            self.scale_slider.setValue(int(ui_scale * 100))
            self.scale_label.setText(f"{int(ui_scale * 100)}%")

            font_index = self.font_combo.findText(self._configured_font_family())
            if font_index >= 0:
                self.font_combo.setCurrentIndex(font_index)

            font_size = self.display_settings.font_size if self.display_settings is not None else self.config.get_font_size()
            self.font_size_spin.setValue(font_size)

            auto_hide = self.display_settings.get_menu_bar_auto_hide() if self.display_settings is not None else self.config.get_menu_bar_auto_hide()
            self.auto_hide_check.setChecked(auto_hide)

            blur_art = self.display_settings.get_blur_explicit_art() if self.display_settings is not None else self.config.get_blur_explicit_art()
            self.blur_art_check.setChecked(blur_art)

            censor_words = self.display_settings.get_censor_explicit_words() if self.display_settings is not None else self.config.get_censor_explicit_words()
            self.censor_words_check.setChecked(censor_words)

            # Audio settings
            exclusive = getattr(self.music_player, "exclusive_mode", False) if self.music_player is not None else self.config.get_exclusive_mode()
            self.exclusive_mode_check.setChecked(bool(exclusive))

            if self.music_player is not None:
                self.normalization_check.setChecked(bool(getattr(self.music_player, "normalization_enabled", False)))
                self.normalization_spin.setValue(getattr(self.music_player, "normalization_target", -14.0))
                self.normalization_spin.setEnabled(self.normalization_check.isChecked())

            self.waveform_log_scale_check.setChecked(self.config.get_waveform_display_mode() == "log")

            # Logging settings
            level_name = logging.getLevelName(self.config.get_logging_level())
            log_index = self.log_level_combo.findText(level_name)
            if log_index >= 0:
                self.log_level_combo.setCurrentIndex(log_index)

            self.console_logging_check.setChecked(self.config.is_console_logging_enabled())
            self.file_logging_check.setChecked(self.config.is_file_logging_enabled())
            self.max_file_size_spin.setValue(self.config.get_max_file_size_mb())
            self.backup_count_spin.setValue(self.config.get_backup_count())

        except (ValueError, TypeError, RuntimeError) as e:
            logger.error(f"Failed to load settings: {e}")
            self._show_error(f"Failed to load settings: {e}")
        finally:
            for control in live_controls:
                control.blockSignals(False)
            self._pending_scale_value = None

    def _apply_settings(self) -> bool:
        """Save all settings from the UI back to config; return True on success."""
        try:
            dir_text = self.dir_display.text()
            if dir_text and Path(dir_text).exists():
                self.config.set_base_directory(dir_text)
            else:
                show_status_message(self, "The selected music directory does not exist. Using current directory.")

            self.config.set_volume(self.volume_slider.value())
            self.config.set_persist_queue(self.queue_persist_check.isChecked())

            # Appearance settings already apply live through DisplaySettings when it's available.
            if self.display_settings is None:
                self.config.set_theme_file(f"{self.theme_combo.currentText()}.qss")
                self.config.set_ui_scale(self.scale_slider.value() / 100.0)
                self.config.set_font_family(self.font_combo.currentText())
                self.config.set_font_size(self.font_size_spin.value())
                self.config.set_menu_bar_auto_hide(self.auto_hide_check.isChecked())
                self.config.set_blur_explicit_art(self.blur_art_check.isChecked())
                self.config.set_censor_explicit_words(self.censor_words_check.isChecked())

            exclusive_mode = self.exclusive_mode_check.isChecked()
            self.config.set_exclusive_mode(exclusive_mode)

            if self.music_player is not None:
                if self.device_combo is not None:
                    device_data = self.device_combo.currentData()
                    if exclusive_mode and device_data:
                        device_info = next((d for d in self.music_player.get_audio_devices() if d["id"] == device_data), None)
                        if device_info is not None and not device_info.get("direct"):
                            show_status_message(self, f'"{device_info["name"]}" is a shared device — bit-perfect mode needs a direct hardware device labeled "(Direct)".', 6000)
                    self.music_player.set_audio_device(device_data)
                    self.config.set_output_device(str(device_data) if device_data else "default")

                self.music_player.set_exclusive_mode(exclusive_mode)

                self.music_player.enable_normalization(self.normalization_check.isChecked())
                self.music_player.set_normalization_target(self.normalization_spin.value())

            self.config.set_waveform_display_mode("log" if self.waveform_log_scale_check.isChecked() else "linear")

            self.config.set_logging_level(self.log_level_combo.currentText())
            self.config.set_console_logging_enabled(self.console_logging_check.isChecked())
            self.config.set_file_logging_enabled(self.file_logging_check.isChecked())
            self.config.set_max_file_size_mb(self.max_file_size_spin.value())
            self.config.set_backup_count(self.backup_count_spin.value())

            self.config.save()

            # Reconfigure logging immediately so the new level takes effect right away
            reconfigure_logging(self.config)

            logger.info("General settings saved and applied")
            show_status_message(self, "Settings saved successfully.")
            return True

        except (ValueError, KeyError, RuntimeError) as e:
            logger.error(f"Failed to apply settings: {e}")
            self._show_error(f"Failed to apply settings: {e}")
            return False

    def _apply_clicked(self):
        """Apply settings, commit the previewed scale, and make the result the new Cancel baseline."""
        if self._apply_settings():
            self._commit_scale_if_pending()
            self._snapshot_live_appearance()

    def _ok_clicked(self):
        """Apply settings and close the dialog only if they saved."""
        if self._apply_settings():
            self.accept()

    def _browse_directory(self):
        """Open a folder picker and update the directory display."""
        current = self.dir_display.text()
        directory = QFileDialog.getExistingDirectory(self, "Select Music Library Directory", current if current else str(Path.home()))
        if directory:
            self.dir_display.setText(directory)
