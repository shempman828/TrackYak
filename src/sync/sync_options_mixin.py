"""SyncOptionsMixin: the Sync view's Options page (destination, files on the destination, MP3 conversion)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.segmented_control import SegmentedControl
from src.common.widgets.style_utils import set_style_property
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.sync.mtp_manager import DEFAULT_MUSIC_PATH, mtp_available, normalize_music_path
from src.sync.transcode import ALLOWED_BITRATES, DEFAULT_BITRATE, TranscodeCache, ffmpeg_available

# Presets for the editable "Music folder" field. A path is inside internal storage unless it
# starts with the name of another storage volume as the phone shows it (e.g. "SD card/Music").
COMMON_DEVICE_MUSIC_PATHS = [DEFAULT_MUSIC_PATH]

# Segments of self.destination_mode. A profile is an Android profile exactly when it has a device linked.
ANDROID_MODE, FOLDER_MODE = 0, 1

# Segments of self.cleanup_mode -> (label, clear_before_sync, prune_untracked, caption).
KEEP_MODE, PRUNE_MODE, WIPE_MODE = 0, 1, 2
CLEANUP_MODES = [
    ("Keep", False, False, "Files that are already on the destination stay there. Only missing tracks are copied."),
    ("Remove untracked", False, True, "After the sync, tracks and playlists that are no longer in this profile are deleted from the destination."),
    ("Wipe, then copy", True, False, "The music and playlist folders on the destination are emptied before every sync, and then everything is copied again. This is slow."),
]

# The cache-limit spin box writes config.ini only after the value has settled for this long.
_CACHE_LIMIT_SAVE_DELAY_MS = 600


class SyncOptionsMixin:
    """Build the Options page and persist its edits into the current profile (and app config for the cache cap)."""

    # Host provides: current_profile, profiles, profile_store, _refresh_current_card(), _refresh_header(),
    # _update_selected_items(), _update_sync_button_state(), _link_device(), _unlink_device().

    # -----------------------------------------------------------------------
    # Construction
    # -----------------------------------------------------------------------

    def _build_settings_tab(self) -> QWidget:
        """The Options page, wrapped in a scroll area so a short pane scrolls instead of crunching."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(12)

        layout.addWidget(self._build_destination_card())

        cleanup_card = DetailCard("Files on the destination")
        self.cleanup_mode = SegmentedControl([label for label, *_ in CLEANUP_MODES])
        self.cleanup_mode.currentIndexChanged.connect(self._on_cleanup_mode_changed)
        cleanup_card.body.addWidget(self.cleanup_mode, 0, Qt.AlignLeft)
        self.cleanup_caption = QLabel(CLEANUP_MODES[KEEP_MODE][3])
        self.cleanup_caption.setObjectName("SyncOptionCaption")
        self.cleanup_caption.setWordWrap(True)
        cleanup_card.body.addWidget(self.cleanup_caption)
        layout.addWidget(cleanup_card)

        layout.addWidget(self._build_conversion_card())
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("SyncScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll

    def _build_destination_card(self) -> DetailCard:
        """The Destination card: Android/Folder switch plus each mode's fields."""
        card = DetailCard("Destination")

        self.destination_mode = SegmentedControl(["Android device", "Folder"])
        if not mtp_available():
            self.destination_mode.button(ANDROID_MODE).setEnabled(False)
            self.destination_mode.setItemToolTip(ANDROID_MODE, "Install gvfs-backends to enable MTP device syncing")
        self.destination_mode.currentIndexChanged.connect(self._on_destination_mode_changed)
        card.body.addWidget(self.destination_mode, 0, Qt.AlignLeft)

        self.android_group = QWidget()
        self.android_group.setProperty("bgTransparent", True)
        android = QGridLayout(self.android_group)
        android.setContentsMargins(0, 8, 0, 0)
        android.setHorizontalSpacing(12)
        android.setVerticalSpacing(8)
        android.setColumnStretch(1, 1)

        android.addWidget(self._field_label("Device"), 0, 0)
        self.linked_device_label = QLabel("No device linked")
        android.addWidget(self.linked_device_label, 0, 1)
        self.link_device_btn = QPushButton("Link device…")
        self.link_device_btn.setEnabled(mtp_available())
        self.link_device_btn.setToolTip("Choose from connected Android devices" if mtp_available() else "Install gvfs-backends to enable MTP device syncing")
        self.link_device_btn.clicked.connect(self._link_device)
        android.addWidget(self.link_device_btn, 0, 2)

        android.addWidget(self._field_label("Music folder"), 1, 0)
        self.music_path_edit = QComboBox()
        self.music_path_edit.setEditable(True)
        self.music_path_edit.setInsertPolicy(QComboBox.NoInsert)
        self.music_path_edit.addItems(COMMON_DEVICE_MUSIC_PATHS)
        self.music_path_edit.lineEdit().setPlaceholderText("Music   (or e.g. SD card/Music)")
        self.music_path_edit.setToolTip("A folder in the phone's internal storage. To use another storage, start the path with its name as the phone shows it, for example “SD card/Music”.")
        self.music_path_edit.textActivated.connect(self._on_music_path_changed)
        self.music_path_edit.lineEdit().editingFinished.connect(self._on_music_path_changed)
        android.addWidget(self.music_path_edit, 1, 1, 1, 2)

        android.addWidget(self._field_label("Nickname"), 2, 0)
        self.device_nickname_edit = QLineEdit()
        self.device_nickname_edit.setPlaceholderText("e.g. My Pixel  (replaces the detected name)")
        self.device_nickname_edit.editingFinished.connect(self._on_nickname_changed)
        android.addWidget(self.device_nickname_edit, 2, 1, 1, 2)
        card.body.addWidget(self.android_group)

        self.folder_group = QWidget()
        self.folder_group.setProperty("bgTransparent", True)
        folder = QHBoxLayout(self.folder_group)
        folder.setContentsMargins(0, 8, 0, 0)
        folder.setSpacing(12)
        folder.addWidget(self._field_label("Folder"))
        self.folder_label = QLabel("No folder set")
        self.folder_label.setProperty("textRole", "muted")
        self.folder_label.setWordWrap(True)
        folder.addWidget(self.folder_label, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse_folder)
        folder.addWidget(self.browse_btn)
        card.body.addWidget(self.folder_group)
        self.android_group.setVisible(False)
        return card

    def _build_conversion_card(self) -> DetailCard:
        """The MP3 conversion card: toggle, bitrate, and the shared cache cap / clear button."""
        card = DetailCard("MP3 conversion")
        ffmpeg_ok = ffmpeg_available()
        self.transcode_mp3_check = QCheckBox("Convert lossless files to MP3")
        self.transcode_mp3_check.setEnabled(ffmpeg_ok)
        self.transcode_mp3_check.setToolTip(
            "FLAC / WAV / AIFF are re-encoded to CBR MP3 as they're copied to the device." if ffmpeg_ok else "Requires ffmpeg on your PATH  (e.g. sudo apt install ffmpeg)"
        )
        self.transcode_mp3_check.toggled.connect(self._on_option_changed)
        card.body.addWidget(self.transcode_mp3_check)

        conversion = QGridLayout()
        conversion.setContentsMargins(0, 4, 0, 0)
        conversion.setHorizontalSpacing(12)
        conversion.setVerticalSpacing(10)
        conversion.setColumnStretch(2, 1)

        conversion.addWidget(self._field_label("Bitrate"), 0, 0)
        # Kept as `bitrate_combo`: SegmentedControl speaks the QComboBox API.
        self.bitrate_combo = SegmentedControl([b.rstrip("k") for b in ALLOWED_BITRATES])
        self.bitrate_combo.setEnabled(False)
        self.bitrate_combo.currentTextChanged.connect(self._on_option_changed)
        conversion.addWidget(self.bitrate_combo, 0, 1)
        kbps = QLabel("kbps")
        kbps.setProperty("textRole", "muted")
        conversion.addWidget(kbps, 0, 2)

        # Global, not per-profile (the cache dir is shared), so _load_options_into_ui never touches it.
        conversion.addWidget(self._field_label("Cache limit"), 1, 0)
        self.cache_max_spin = QSpinBox()
        self.cache_max_spin.setRange(0, 102400)
        self.cache_max_spin.setSingleStep(256)
        self.cache_max_spin.setSuffix(" MB")
        self.cache_max_spin.setSpecialValueText("Unlimited")
        self.cache_max_spin.setValue(app_config.get_transcode_cache_max_mb())
        self.cache_max_spin.setToolTip("After a sync that converts to MP3, the oldest unused conversions are deleted until the cache is back under this size. 0 = no limit.")
        self._cache_save_timer = QTimer(self.cache_max_spin)
        self._cache_save_timer.setSingleShot(True)
        self._cache_save_timer.setInterval(_CACHE_LIMIT_SAVE_DELAY_MS)
        self._cache_save_timer.timeout.connect(self._flush_cache_max)
        self.cache_max_spin.valueChanged.connect(self._on_cache_max_changed)
        conversion.addWidget(self.cache_max_spin, 1, 1)
        self.clear_cache_btn = QPushButton("Clear MP3 cache")
        self.clear_cache_btn.clicked.connect(self._clear_transcode_cache)
        conversion.addWidget(self.clear_cache_btn, 1, 2, Qt.AlignLeft)
        card.body.addLayout(conversion)

        caption = QLabel("Lossy files (MP3, AAC, M4A, OGG) copy unchanged. Your library's original files are never modified.")
        caption.setProperty("textRole", "muted")
        caption.setWordWrap(True)
        card.body.addWidget(caption)
        return card

    @staticmethod
    def _field_label(text: str) -> QLabel:
        """A form-field label in the shared fieldLabel style."""
        label = QLabel(text)
        label.setProperty("textRole", "fieldLabel")
        return label

    # -----------------------------------------------------------------------
    # Loading a profile
    # -----------------------------------------------------------------------

    def _load_options_into_ui(self):
        """Show the current profile's options without firing their change handlers."""
        p = self.current_profile

        self.device_nickname_edit.blockSignals(True)
        self.device_nickname_edit.setText(p.device_name)
        self.device_nickname_edit.blockSignals(False)

        self.music_path_edit.blockSignals(True)
        self.music_path_edit.setCurrentText(p.music_path)
        self.music_path_edit.blockSignals(False)

        self.folder_label.setText(p.path or "No folder set")
        set_style_property(self.folder_label, "textRole", None if p.path else "muted")

        self._sync_destination_mode_control()

        if p.clear_before_sync:
            cleanup = WIPE_MODE
        elif p.prune_untracked:
            cleanup = PRUNE_MODE
        else:
            cleanup = KEEP_MODE
        self.cleanup_mode.blockSignals(True)
        self.cleanup_mode.setCurrentIndex(cleanup)
        self.cleanup_mode.blockSignals(False)
        self._update_cleanup_caption(cleanup)

        ffmpeg_ok = ffmpeg_available()
        self.transcode_mp3_check.blockSignals(True)
        self.transcode_mp3_check.setChecked(p.transcode_to_mp3)
        self.transcode_mp3_check.setEnabled(ffmpeg_ok)
        self.transcode_mp3_check.blockSignals(False)

        self.bitrate_combo.blockSignals(True)
        self.bitrate_combo.setCurrentText((p.transcode_bitrate or DEFAULT_BITRATE).rstrip("k"))
        self.bitrate_combo.setEnabled(ffmpeg_ok and p.transcode_to_mp3)
        self.bitrate_combo.blockSignals(False)

        self._update_cache_button_label()

    def _sync_destination_mode_control(self):
        """Point the Android/Folder switch (and its sections) at the profile's real mode."""
        is_mtp = bool(self.current_profile and self.current_profile.is_mtp)
        self.destination_mode.blockSignals(True)
        self.destination_mode.setCurrentIndex(ANDROID_MODE if is_mtp else FOLDER_MODE)
        self.destination_mode.blockSignals(False)
        self._show_destination_section(is_mtp)

    def _show_destination_section(self, android: bool):
        """Show the Android fields or the Folder fields."""
        self.android_group.setVisible(android)
        self.folder_group.setVisible(not android)

    # -----------------------------------------------------------------------
    # Handlers
    # -----------------------------------------------------------------------

    def _on_destination_mode_changed(self, index: int):
        """Switch the profile between folder and Android (Android only sticks once a device is linked)."""
        if not self.current_profile:
            return
        if index == FOLDER_MODE:
            self._show_destination_section(False)
            if self.current_profile.is_mtp:
                self._unlink_device()
        elif not self.current_profile.is_mtp:
            # _on_link_devices_listed reverts the switch if no device gets linked.
            self._show_destination_section(True)
            self._link_device()

    def _on_nickname_changed(self):
        """Persist the device nickname."""
        if not self.current_profile:
            return
        self.current_profile.device_name = self.device_nickname_edit.text().strip()
        self._refresh_current_card()
        self.profile_store.save(self.profiles)
        self._refresh_header()

    def _on_music_path_changed(self, *_):
        """Normalize and persist the on-device music folder."""
        if not self.current_profile:
            return
        # Empty or ".." paths would put files (and prune) at the storage root.
        new_path = normalize_music_path(self.music_path_edit.currentText())
        if self.music_path_edit.currentText() != new_path:
            self.music_path_edit.blockSignals(True)
            self.music_path_edit.setCurrentText(new_path)
            self.music_path_edit.blockSignals(False)
        if new_path == self.current_profile.music_path:
            return
        self.current_profile.music_path = new_path
        self.profile_store.save(self.profiles)
        self._refresh_current_card()
        self._refresh_header()

    def _on_cleanup_mode_changed(self, index: int):
        """Persist the Keep / Remove untracked / Wipe choice."""
        self._update_cleanup_caption(index)
        if not self.current_profile:
            return
        _label, clear, prune, _caption = CLEANUP_MODES[index]
        self.current_profile.clear_before_sync = clear
        self.current_profile.prune_untracked = prune
        self.profile_store.save(self.profiles)

    def _update_cleanup_caption(self, index: int):
        """Explain the selected cleanup mode (in the warning tone for Wipe)."""
        self.cleanup_caption.setText(CLEANUP_MODES[index][3])
        set_style_property(self.cleanup_caption, "tone", "warn" if index == WIPE_MODE else None)

    def _on_option_changed(self):
        """Persist the MP3 toggle and bitrate, and re-estimate the selection size."""
        if not self.current_profile:
            return
        self.current_profile.transcode_to_mp3 = self.transcode_mp3_check.isChecked()
        self.current_profile.transcode_bitrate = f"{self.bitrate_combo.currentText()}k"
        self.bitrate_combo.setEnabled(self.transcode_mp3_check.isEnabled() and self.transcode_mp3_check.isChecked())
        self.profile_store.save(self.profiles)
        # The summary shows a post-conversion estimate while transcoding is on.
        self._update_selected_items()

    def _sync_running(self) -> bool:
        """True while a SyncWorker is running."""
        worker = getattr(self, "sync_worker", None)
        return worker is not None and worker.isRunning()

    def _update_cache_button_label(self):
        """Show the transcode-cache size on the Clear button (disabled while empty or syncing)."""
        size_mb = TranscodeCache().size_bytes() / (1024 * 1024)
        self.clear_cache_btn.setText(f"Clear MP3 cache ({size_mb:.0f} MB)")
        self.clear_cache_btn.setEnabled(size_mb > 0 and not self._sync_running())

    def _on_cache_max_changed(self, value_mb: int):
        """Apply the cache cap now; write config.ini once the value settles."""
        app_config.set_transcode_cache_max_mb(value_mb)
        self._cache_save_timer.start()

    def _flush_cache_max(self):
        """Write a pending cache-cap change to disk."""
        self._cache_save_timer.stop()
        app_config.save()

    def _clear_transcode_cache(self):
        """Ask, then delete every cached MP3 conversion."""
        if self._sync_running():
            return  # the running sync may be reading these files
        cache = TranscodeCache()
        size_mb = cache.size_bytes() / (1024 * 1024)
        reply = QMessageBox.question(
            self, "Clear MP3 cache", f"Delete {size_mb:.0f} MB of cached MP3 conversions?\n\nThey are re-created automatically on the next sync that needs them.", QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        removed = cache.clear()
        logger.info(f"Cleared transcode cache: {removed} file(s)")
        show_status_message(self, f"Cleared {removed} cached MP3 file(s).")
        self._update_cache_button_label()

    def _browse_folder(self):
        """Pick the destination folder for a folder profile."""
        if not self.current_profile:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Sync Destination Folder", self.current_profile.path or "", QFileDialog.ShowDirsOnly)
        if not folder:
            return
        logger.info(f"Sync destination folder set to '{folder}' for profile '{self.current_profile.name}'")
        self.current_profile.path = folder
        self.folder_label.setText(folder)
        set_style_property(self.folder_label, "textRole", None)
        self._refresh_current_card()
        self.profile_store.save(self.profiles)
        self._refresh_header()
        self._update_sync_button_state()
