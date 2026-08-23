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

from src.core.config_setup import Config
from src.core.font_family_worker import FontFamilyWorker
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.display.display_settings import DisplaySettings


class ConfigDialog(QDialog):
    """General Settings dialog — covers Library, Playback, Appearance (incl.
    display/theme/explicit-content), Audio (incl. device/normalization), and
    Logging. Everything except a handful of live-preview Appearance controls
    is applied on Apply/OK."""

    # Set by the first FontFamilyWorker to finish; later dialog opens reuse
    # it instead of re-running fc-list (see _create_appearance_tab).
    _canonical_font_families_cache: set | None = None

    def __init__(
        self,
        config: Config,
        display_settings: DisplaySettings | None = None,
        music_player=None,
        parent=None,
    ):
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
        # The scale-slider preview above only restyles what's actually on
        # screen (see _visible_restyle_roots); commit the real, full-app
        # change exactly once when the dialog closes, however it closes.
        self.finished.connect(self._commit_scale_if_pending)
        self.finished.connect(self._cleanup_font_worker)

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

        # Fade duration (0.0-10.0 seconds, 0.1s steps)
        fade_layout = QHBoxLayout()
        self.fade_slider = QSlider(Qt.Horizontal)
        self.fade_slider.setRange(0, 100)
        self.fade_label = QLabel("0.0s")
        self.fade_slider.valueChanged.connect(
            lambda v: self.fade_label.setText(f"{v / 10:.1f}s")
        )
        fade_layout.addWidget(self.fade_slider)
        fade_layout.addWidget(self.fade_label)
        layout.addRow("Fade Duration:", fade_layout)

        # Queue persistence
        self.queue_persist_check = QCheckBox("Remember queue between sessions")
        layout.addRow("Queue:", self.queue_persist_check)

        return widget

    def _create_appearance_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Theme selection — live-applies via DisplaySettings when available,
        # and keeps the startup theme_file config key in sync either way.
        self.theme_combo = QComboBox()
        if self.display_settings is not None:
            available_themes = self.display_settings.get_available_themes()
        else:
            available_themes = [
                Path(name).stem for name in self.config.get_available_themes()
            ]
        self.theme_combo.addItems(available_themes if available_themes else ["default"])
        if self.display_settings is not None:
            self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addRow("Theme:", self.theme_combo)

        # UI scale
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(80, 140)  # percent
        self.scale_label = QLabel()
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addWidget(self.scale_label)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        layout.addRow("UI Scale:", scale_layout)

        # Font family / script filter
        # Qt's QFontDatabase.families() returns one entry per fontconfig-
        # registered "named instance" of a variable font, not one per
        # typeface — e.g. Noto Sans alone contributes ~900 entries (one per
        # weight/width) in addition to the real "Noto Sans" family, which
        # already exposes those as styles(). We filter those aliases out
        # and let the Script combo narrow the (still large) remainder by
        # writing system, since most users only care about one script.
        self.font_script_combo = QComboBox()
        self.font_script_combo.addItem("Any", QFontDatabase.WritingSystem.Any)
        for ws in QFontDatabase.writingSystems():
            self.font_script_combo.addItem(QFontDatabase.writingSystemName(ws), ws)
        self.font_script_combo.currentIndexChanged.connect(
            lambda _: self._populate_font_combo()
        )

        self.font_combo = QComboBox()
        if ConfigDialog._canonical_font_families_cache is not None:
            self._canonical_font_families = ConfigDialog._canonical_font_families_cache
            self._populate_font_combo()
        else:
            # fc-list clustering (see FontFamilyWorker) is slow enough to be
            # noticeable on the very first Settings open of a session — show
            # a placeholder and fill in for real once the background
            # computation lands, instead of blocking dialog construction.
            self._canonical_font_families = set()
            self.font_combo.addItem("Loading fonts…")
            self.font_combo.setEnabled(False)
            self.font_script_combo.setEnabled(False)
            self._font_family_worker = FontFamilyWorker(parent=self)
            self._font_family_worker.computed.connect(
                self._on_font_families_computed
            )
            self._font_family_worker.start()
        if self.display_settings is not None:
            self.font_combo.currentTextChanged.connect(
                self.display_settings.set_font_family
            )

        font_layout = QHBoxLayout()
        font_layout.addWidget(self.font_combo, 2)
        font_layout.addWidget(QLabel("Script:"))
        font_layout.addWidget(self.font_script_combo, 1)
        layout.addRow("Font:", font_layout)

        # Font size
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(7, 20)
        if self.display_settings is not None:
            self.font_size_spin.valueChanged.connect(
                self.display_settings.set_font_size
            )
        layout.addRow("Font Size:", self.font_size_spin)

        # ---- Menu Bar behaviour ----
        menu_bar_label = QLabel("Menu Bar")
        menu_bar_label.setProperty("title", True)
        layout.addRow(menu_bar_label)

        self.auto_hide_check = QCheckBox("Auto-hide menu bar (shows on mouse-over)")
        self.auto_hide_check.setToolTip(
            "When enabled, the menu bar will hide automatically. "
            "Move your mouse to the top of the window to reveal it."
        )
        if self.display_settings is not None:
            self.auto_hide_check.toggled.connect(
                self.display_settings.set_menu_bar_auto_hide
            )
        layout.addRow("", self.auto_hide_check)

        # ---- Explicit content ----
        content_label = QLabel("Explicit Content")
        content_label.setProperty("title", True)
        layout.addRow(content_label)

        self.blur_art_check = QCheckBox("Blur album art marked explicit")
        self.blur_art_check.setToolTip(
            "When enabled, cover/liner art for albums marked as having "
            "explicit art is shown blurred until you choose to reveal it."
        )
        if self.display_settings is not None:
            self.blur_art_check.toggled.connect(
                self.display_settings.set_blur_explicit_art
            )
        layout.addRow("", self.blur_art_check)

        self.censor_words_check = QCheckBox("Censor explicit words")
        self.censor_words_check.setToolTip(
            "When enabled, words listed in assets/explicit_words.txt are "
            "masked with asterisks wherever text is displayed (lyrics, "
            "album titles, track titles, etc.)."
        )
        if self.display_settings is not None:
            self.censor_words_check.toggled.connect(
                self.display_settings.set_censor_explicit_words
            )
        layout.addRow("", self.censor_words_check)

        return widget

    def _create_audio_tab(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        # Audio output device — only meaningful with a live player
        self.device_combo = None
        if self.music_player is not None:
            self.device_combo = QComboBox()
            self._load_audio_devices()
            layout.addRow("Output Device:", self.device_combo)

        # Buffer size
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItems(["256", "512", "1024", "2048"])
        layout.addRow("Buffer Size:", self.buffer_combo)

        # ---- Bit-Perfect Playback ----
        self.exclusive_mode_check = QCheckBox("Bit-Perfect / Exclusive Mode")
        layout.addRow("", self.exclusive_mode_check)

        exclusive_info_label = QLabel(
            "Opens the output device directly, with no volume/format processing "
            "in between. Only works with a direct hardware device — pick one "
            "labeled \"(Direct)\" above, not \"(Shared)\". If the device is busy "
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
        layout.addRow("Target Loudness:", self.normalization_spin)

        info_label = QLabel(
            "Normalization adjusts track volumes to a consistent loudness level. "
            "Uses ReplayGain metadata when available."
        )
        info_label.setWordWrap(True)
        info_label.setProperty("textRole", "muted")
        layout.addRow(info_label)

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
    # Appearance tab live-preview slots (only wired when display_settings
    # is available — see _create_appearance_tab)
    # ------------------------------------------------------------------

    def _on_font_families_computed(self, families: set):
        """FontFamilyWorker.computed slot — replaces the "Loading fonts…"
        placeholder with the real, alias-deduplicated list."""
        ConfigDialog._canonical_font_families_cache = families
        self._canonical_font_families = families
        self.font_combo.setEnabled(True)
        self.font_script_combo.setEnabled(True)
        self._populate_font_combo()

        # The placeholder item meant _load_current_settings()'s findText()
        # couldn't have found the configured font — resolve it now.
        font_family = (
            self.display_settings.font_family
            if self.display_settings is not None
            else self.config.get_font_family()
        )
        index = self.font_combo.findText(font_family)
        if index >= 0:
            self.font_combo.setCurrentIndex(index)

    def _cleanup_font_worker(self):
        """Ensure FontFamilyWorker is never left running when the dialog
        closes -- QThread must be joined before its Python wrapper can be
        garbage-collected, or Qt aborts with "Destroyed while thread is
        still running"."""
        worker = getattr(self, "_font_family_worker", None)
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            worker.wait()

    def _populate_font_combo(self):
        """(Re)fill font_combo from the current Script filter, preserving
        the current selection when it's still available and otherwise
        falling back to the first entry (which live-applies via the
        existing currentTextChanged connection, same as any other choice).
        """
        writing_system = self.font_script_combo.currentData()
        families = sorted(
            f
            for f in QFontDatabase.families(writing_system)
            if f in self._canonical_font_families
        )

        previous = self.font_combo.currentText()
        self.font_combo.blockSignals(True)
        self.font_combo.clear()
        self.font_combo.addItems(families)
        self.font_combo.blockSignals(False)

        index = self.font_combo.findText(previous)
        if index < 0 and families:
            index = 0
        if index >= 0:
            self.font_combo.setCurrentIndex(index)

    def _on_theme_changed(self, name: str):
        self.display_settings.set_theme(name)
        # Keep the startup-time theme key in sync with the live one so the
        # next launch opens with what's on screen right now.
        self.config.set_theme_file(f"{name}.qss")
        self.config.save()

    def _on_scale_changed(self, value: int):
        self.scale_label.setText(f"{value}%")
        if self.display_settings is not None:
            self._pending_scale_value = value
            self._scale_debounce_timer.start()

    def _visible_restyle_roots(self) -> list[QWidget]:
        """Widgets to live-restyle during a scale-slider drag: this dialog,
        plus whatever the user can actually see behind it (the active
        QStackedWidget page, docks, menu bar, status bar) -- not every
        widget in the app, most of which (every other tab's grid included)
        isn't on screen at all while this modal dialog is up.
        """
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
        if hasattr(main_window, "statusBar"):
            status_bar = main_window.statusBar()
            if status_bar is not None:
                roots.append(status_bar)
        if hasattr(main_window, "findChildren"):
            roots.extend(main_window.findChildren(QDockWidget))

        return roots

    def _preview_pending_scale(self):
        if self._pending_scale_value is not None and self.display_settings is not None:
            self.display_settings.preview_ui_scale_in(
                self._pending_scale_value / 100.0, self._visible_restyle_roots()
            )

    def _commit_scale_if_pending(self, *_args):
        if self._pending_scale_value is not None and self.display_settings is not None:
            self.display_settings.set_ui_scale(self._pending_scale_value / 100.0)
        self._pending_scale_value = None

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
            current_device = (
                saved_device
                if saved_device != "default"
                else getattr(self.music_player, "current_device", None)
            )

            current_index = 0

            for i, device in enumerate(devices):
                device_name = device.get("name", f"Device {device['id']}")
                is_default = device.get("default", False)

                display_name = device_name
                display_name += " (Direct)" if device.get("direct") else " (Shared)"
                if is_default:
                    display_name += " (Default)"

                self.device_combo.addItem(display_name, device["id"])

                if current_device and (
                    str(device["id"]) == str(current_device)
                    or device_name == current_device
                ):
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
            fade_duration = self.config.get_fade_duration()
            self.fade_slider.setValue(round(fade_duration * 10))
            self.fade_label.setText(f"{fade_duration:.1f}s")

            # Queue persistence
            self.queue_persist_check.setChecked(
                self.config.config.getboolean("queue", "persist_queue", fallback=True)
            )

            # Appearance settings
            if self.display_settings is not None:
                if self.display_settings.theme_name:
                    index = self.theme_combo.findText(self.display_settings.theme_name)
                    if index >= 0:
                        self.theme_combo.setCurrentIndex(index)
            else:
                theme_file = self.config.get_theme_file()
                index = self.theme_combo.findText(Path(theme_file).stem)
                if index >= 0:
                    self.theme_combo.setCurrentIndex(index)

            ui_scale = (
                self.display_settings.ui_scale
                if self.display_settings is not None
                else self.config.get_ui_scale()
            )
            self.scale_slider.setValue(int(ui_scale * 100))
            self.scale_label.setText(f"{int(ui_scale * 100)}%")

            font_family = (
                self.display_settings.font_family
                if self.display_settings is not None
                else self.config.get_font_family()
            )
            font_index = self.font_combo.findText(font_family)
            if font_index >= 0:
                self.font_combo.setCurrentIndex(font_index)

            font_size = (
                self.display_settings.font_size
                if self.display_settings is not None
                else self.config.get_font_size()
            )
            self.font_size_spin.setValue(font_size)

            auto_hide = (
                self.display_settings.get_menu_bar_auto_hide()
                if self.display_settings is not None
                else self.config.config.getboolean(
                    "display", "menu_bar_auto_hide", fallback=False
                )
            )
            self.auto_hide_check.setChecked(auto_hide)

            blur_art = (
                self.display_settings.get_blur_explicit_art()
                if self.display_settings is not None
                else self.config.get_blur_explicit_art()
            )
            self.blur_art_check.setChecked(blur_art)

            censor_words = (
                self.display_settings.get_censor_explicit_words()
                if self.display_settings is not None
                else self.config.get_censor_explicit_words()
            )
            self.censor_words_check.setChecked(censor_words)

            # Audio settings
            buffer_size = self.config.get_buffer_size()
            buffer_map = {256: 0, 512: 1, 1024: 2, 2048: 3}
            self.buffer_combo.setCurrentIndex(buffer_map.get(buffer_size, 2))

            self.exclusive_mode_check.setChecked(
                getattr(self.music_player, "exclusive_mode", None)
                if self.music_player is not None
                else self.config.get_exclusive_mode()
            )

            if self.music_player is not None:
                norm_enabled = getattr(
                    self.music_player, "normalization_enabled", False
                )
                norm_target = getattr(
                    self.music_player, "normalization_target", -14.0
                )
                self.normalization_check.setChecked(norm_enabled)
                self.normalization_spin.setValue(norm_target)

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

        except (ValueError, TypeError, RuntimeError) as e:
            logger.error(f"Failed to load settings: {e}")
            self._show_error(f"Failed to load settings: {e}")

    def _apply_settings(self):
        """Save all settings from the UI back to config."""
        try:
            # Library settings
            dir_text = self.dir_display.text()
            if dir_text and Path(dir_text).exists():
                self.config.set_base_directory(dir_text)
            else:
                show_status_message(
                    self,
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
            fade_duration = self.fade_slider.value() / 10
            self.config.set_fade_duration(fade_duration)

            if self.music_player is not None:
                self.music_player.enable_crossfade(self.crossfade_check.isChecked())
                self.music_player.set_crossfade_duration(round(fade_duration * 1000))

            # Queue persistence
            self.config.config.set(
                "queue",
                "persist_queue",
                str(self.queue_persist_check.isChecked()).lower(),
            )

            # Appearance settings — theme/scale/font/auto-hide/explicit-content
            # already applied live via DisplaySettings when it's available; if
            # not, fall back to writing config directly here.
            if self.display_settings is None:
                self.config.set_theme_file(f"{self.theme_combo.currentText()}.qss")
                self.config.set_ui_scale(self.scale_slider.value() / 100.0)
                self.config.set_font_family(self.font_combo.currentText())
                self.config.set_font_size(self.font_size_spin.value())
                self.config.config.set(
                    "display",
                    "menu_bar_auto_hide",
                    str(self.auto_hide_check.isChecked()).lower(),
                )
                self.config.set_blur_explicit_art(self.blur_art_check.isChecked())
                self.config.set_censor_explicit_words(
                    self.censor_words_check.isChecked()
                )

            # Audio settings
            buffer_size = int(self.buffer_combo.currentText())
            self.config.set_buffer_size(buffer_size)

            exclusive_mode = self.exclusive_mode_check.isChecked()
            self.config.set_exclusive_mode(exclusive_mode)

            if self.music_player is not None:
                if self.device_combo is not None:
                    device_data = self.device_combo.currentData()
                    if exclusive_mode and device_data:
                        device_info = next(
                            (
                                d
                                for d in self.music_player.get_audio_devices()
                                if d["id"] == device_data
                            ),
                            None,
                        )
                        if device_info is not None and not device_info.get("direct"):
                            show_status_message(
                                self,
                                f'"{device_info["name"]}" is a shared device — '
                                "bit-perfect mode needs a direct hardware device "
                                'labeled "(Direct)".',
                                6000,
                            )
                    self.music_player.set_audio_device(device_data)
                    self.config.set_output_device(str(device_data) if device_data else "default")

                self.music_player.set_exclusive_mode(exclusive_mode)

                self.music_player.enable_normalization(
                    self.normalization_check.isChecked()
                )
                self.music_player.set_normalization_target(
                    self.normalization_spin.value()
                )

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
            from src.core.logger_config import reconfigure_logging

            reconfigure_logging(self.config)

            logger.info("General settings saved and applied")
            show_status_message(self, "Settings saved successfully.")

        except (ValueError, KeyError, RuntimeError) as e:
            logger.error(f"Failed to apply settings: {e}")
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
