# sync_view.py

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.segmented_control import SegmentedControl
from src.common.widgets.style_utils import set_style_property
from src.db.db_helpers import Session
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager, show_status_message
from src.sync.device_card import DEVICE_GLYPH, FOLDER_GLYPH, DeviceCard
from src.sync.mtp_list_worker import MtpListWorker
from src.sync.mtp_manager import MtpDevice, MtpManager, mtp_available
from src.sync.sync_activity_panel import SyncActivityPanel
from src.sync.sync_execution_mixin import SyncExecutionMixin
from src.sync.sync_manager import SyncManager
from src.sync.sync_profile import SyncProfile, SyncProfileStore
from src.sync.sync_selection_mixin import SyncSelectionMixin
from src.sync.sync_selection_tree import SyncSelectionTree
from src.sync.sync_worker import SyncWorker
from src.sync.transcode import TranscodeCache, ffmpeg_available

# Common on-device music folders offered in the "Music folder on device"
# dropdown. The field stays editable, so any custom relative path still works.
COMMON_DEVICE_MUSIC_PATHS = [
    MtpManager.DEFAULT_MUSIC_PATH,  # "Music" — internal storage default
    "Internal storage/Music",
    "SD card/Music",
]

# Pages of the detail area (self.tabs) and segments of self.tab_switch.
MUSIC_PAGE, OPTIONS_PAGE, ACTIVITY_PAGE = 0, 1, 2
PAGE_TITLES = ["Music", "Options", "Activity"]

# Segments of self.destination_mode. The mode is not stored: a profile is an
# Android profile exactly when it has a device linked (SyncProfile.is_mtp).
ANDROID_MODE, FOLDER_MODE = 0, 1

# Segments of self.cleanup_mode -> (clear_before_sync, prune_untracked).
KEEP_MODE, PRUNE_MODE, WIPE_MODE = 0, 1, 2
CLEANUP_MODES = [
    (
        "Keep",
        False,
        False,
        "Files that are already on the destination stay there. Only missing tracks are copied.",
    ),
    (
        "Remove untracked",
        False,
        True,
        "After the sync, tracks and playlists that are no longer in this profile are "
        "deleted from the destination.",
    ),
    (
        "Wipe, then copy",
        True,
        False,
        "The music and playlist folders on the destination are emptied before every sync, "
        "and then everything is copied again. This is slow.",
    ),
]

# ---------------------------------------------------------------------------
# SyncView — main view
# ---------------------------------------------------------------------------


class SyncView(SyncSelectionMixin, SyncExecutionMixin, QWidget):
    """
    Device sync view.

    Sidebar     — one DeviceCard per profile, with New / Detect below.
    Detail      — empty state when there are no profiles; otherwise a
                  profile header (name, destination, connection, ⋯ menu)
                  above a segmented switch between three pages:
                  • Music     — filterable playlist/mood checklist
                  • Options   — destination, extra-files policy, MP3 conversion
                  • Activity  — result of the last sync run
    Bottom bar  — selection summary + Sync now; during a sync, the current
                  step, a progress bar and Cancel; afterwards, the result.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        # Pass the scoped_session proxy itself, not a resolved Session --
        # SyncManager's calls happen from both this (main) thread and
        # SyncWorker's background thread. A resolved Session() is pinned to
        # whichever thread called it, so handing that concrete object to a
        # long-lived SyncManager used from both threads meant SyncWorker was
        # silently reusing the main thread's Session cross-thread (not
        # thread-safe). The proxy resolves to each calling thread's own
        # Session instead, matching how every other controller.* helper is
        # wired (see MusicController.__init__).
        self.sync_manager = SyncManager(Session)
        self.profile_store = SyncProfileStore()
        self.mtp_manager = MtpManager()

        self.profiles: list[SyncProfile] = []
        self.current_profile: SyncProfile | None = None
        self.cards: list[DeviceCard] = []
        self.selected_card: DeviceCard | None = None
        self.sync_worker: SyncWorker | None = None
        self.status_manager = StatusManager
        self.selected_items: list[dict] = []
        self._selection_totals = (0, 0, 0, 0.0)

        # MTP device enumeration runs on throwaway threads (MtpListWorker):
        # `gio` can block its caller indefinitely against a wedged device.
        # _known_mtp_devices caches the most recent scan for _refresh_header.
        # The poll, the Link picker and Detect each get their own worker so
        # a user action is never dropped because a poll is in flight.
        self._known_mtp_devices: list[MtpDevice] = []
        self._mtp_list_worker: MtpListWorker | None = None
        self._link_worker: MtpListWorker | None = None
        self._detect_worker: MtpListWorker | None = None

        # Periodic MTP poll (every 5 s) to update connection badges
        self._mtp_poll_timer = QTimer(self)
        self._mtp_poll_timer.timeout.connect(self._refresh_mtp_devices)
        if mtp_available():
            self._mtp_poll_timer.start(5000)

        self._init_ui()
        self._load_profiles()
        self._refresh_sync_items()

    def showEvent(self, event):
        """Refresh playlists/moods every time this view is shown — catches new ones."""
        super().showEvent(event)
        self._refresh_sync_items()

    def resizeEvent(self, event):
        """Re-elide the header's destination path to the new width."""
        super().resizeEvent(event)
        if self.current_profile:
            self._refresh_header()

    def closeEvent(self, event):
        """Cancel and join the background sync-items load before teardown."""
        loader = getattr(self, "_sync_items_loader", None)
        if loader is not None and loader.isRunning():
            loader.request_cancel()
            loader.wait()
        super().closeEvent(event)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)
        self.splitter.addWidget(self._build_sidebar())
        self.splitter.addWidget(self._build_detail_panel())
        self.splitter.setSizes([270, 700])
        root.addWidget(self.splitter, 1)

        root.addWidget(self._build_bottom_bar())

    # -- Sidebar -------------------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("SyncSidebar")
        sidebar.setMinimumWidth(230)
        sidebar.setMaximumWidth(340)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 14, 8, 12)
        layout.setSpacing(8)

        section_lbl = QLabel("DEVICES")
        section_lbl.setObjectName("SyncSectionLabel")
        layout.addWidget(section_lbl)

        scroll = QScrollArea()
        scroll.setObjectName("SyncScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.card_container = QWidget()
        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(6)
        self.card_layout.addStretch()

        scroll.setWidget(self.card_container)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.add_profile_btn = QPushButton("+ New")
        self.add_profile_btn.setToolTip("Create a new sync profile")
        self.add_profile_btn.clicked.connect(self._new_profile)
        btn_row.addWidget(self.add_profile_btn, 1)

        self.detect_btn = QPushButton("⟳ Detect")
        self.detect_btn.setToolTip(
            "Scan for connected Android devices via USB"
            if mtp_available()
            else "Install gvfs-backends (sudo apt install gvfs-backends) to enable device detection"
        )
        self.detect_btn.setEnabled(mtp_available())
        self.detect_btn.clicked.connect(self._detect_devices)
        btn_row.addWidget(self.detect_btn, 1)

        layout.addLayout(btn_row)
        return sidebar

    # -- Detail panel --------------------------------------------------------

    def _build_detail_panel(self) -> QWidget:
        self.detail_panel = QWidget()
        self.detail_panel.setObjectName("SyncDetail")
        layout = QVBoxLayout(self.detail_panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.detail_stack = QStackedWidget()
        self.placeholder = self._build_empty_state()
        self.detail_stack.addWidget(self.placeholder)

        self.profile_page = QWidget()
        page_layout = QVBoxLayout(self.profile_page)
        page_layout.setContentsMargins(20, 16, 20, 12)
        page_layout.setSpacing(14)
        page_layout.addWidget(self._build_header())

        self.tab_switch = SegmentedControl(PAGE_TITLES)
        self.tab_switch.currentIndexChanged.connect(self._on_page_changed)
        page_layout.addWidget(self.tab_switch, 0, Qt.AlignLeft)

        self.tabs = QStackedWidget()
        self.tabs.addWidget(self._build_selection_tab())
        self.tabs.addWidget(self._build_settings_tab())
        self.tabs.addWidget(self._build_log_tab())
        page_layout.addWidget(self.tabs, 1)

        self.detail_stack.addWidget(self.profile_page)
        layout.addWidget(self.detail_stack)
        return self.detail_panel

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("SyncEmptyCard")
        card.setFixedWidth(460)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(10)

        icon = QLabel(DEVICE_GLYPH)
        icon.setObjectName("SyncEmptyIcon")
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)

        title = QLabel("Sync music to a phone or a folder")
        title.setObjectName("SyncEmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        body = QLabel(
            "Make one profile for each device. Pick the playlists and moods to copy, "
            "and each sync keeps the device up to date."
        )
        body.setProperty("textRole", "muted")
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.empty_detect_btn = QPushButton("Detect Android device")
        self.empty_detect_btn.setObjectName("PrimaryButton")
        self.empty_detect_btn.setEnabled(mtp_available())
        self.empty_detect_btn.setToolTip(
            "Connect the phone with USB and set it to File Transfer mode."
            if mtp_available()
            else "Install gvfs-backends (sudo apt install gvfs-backends) to enable device detection"
        )
        self.empty_detect_btn.clicked.connect(self._detect_devices)
        buttons.addWidget(self.empty_detect_btn, 1)

        folder_btn = QPushButton("Choose folder…")
        folder_btn.clicked.connect(self._new_folder_profile)
        buttons.addWidget(folder_btn, 1)
        layout.addLayout(buttons)

        blank_btn = QPushButton("Create an empty profile")
        blank_btn.setProperty("linkButton", True)
        blank_btn.setCursor(Qt.PointingHandCursor)
        blank_btn.clicked.connect(self._new_profile)
        layout.addWidget(blank_btn, 0, Qt.AlignCenter)

        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(2)
        return page

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("SyncProfileHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.profile_icon = QLabel()
        self.profile_icon.setObjectName("SyncProfileIcon")
        self.profile_icon.setFixedSize(48, 48)
        self.profile_icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.profile_icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        self.profile_title = QLabel()
        self.profile_title.setObjectName("SyncProfileTitle")
        text_col.addWidget(self.profile_title)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.device_label = QLabel()
        self.device_label.setObjectName("SyncStatusPill")
        self.device_label.setProperty("linkState", "idle")
        status_row.addWidget(self.device_label)
        self.destination_label = QLabel()
        self.destination_label.setProperty("textRole", "muted")
        self.destination_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status_row.addWidget(self.destination_label, 1)
        text_col.addLayout(status_row)
        layout.addLayout(text_col, 1)

        self.change_destination_btn = QPushButton("Change…")
        self.change_destination_btn.clicked.connect(self._change_destination)
        layout.addWidget(self.change_destination_btn, 0, Qt.AlignVCenter)

        self.profile_menu_btn = QToolButton()
        self.profile_menu_btn.setObjectName("SyncProfileMenuButton")
        self.profile_menu_btn.setText("⋯")
        self.profile_menu_btn.setToolTip("Profile actions")
        self.profile_menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.profile_menu_btn)
        rename_action = QAction("Rename…", menu)
        rename_action.triggered.connect(self._rename_profile)
        delete_action = QAction("Delete profile…", menu)
        delete_action.triggered.connect(self._delete_profile)
        menu.addAction(rename_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        self.profile_menu_btn.setMenu(menu)
        layout.addWidget(self.profile_menu_btn, 0, Qt.AlignVCenter)

        return header

    def _build_selection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.sync_filter_edit = QLineEdit()
        self.sync_filter_edit.setPlaceholderText("Filter playlists and moods…")
        self.sync_filter_edit.setClearButtonEnabled(True)
        self.sync_filter_edit.textChanged.connect(self._on_sync_filter_changed)
        toolbar.addWidget(self.sync_filter_edit, 1)
        toolbar.addSpacing(8)

        def link(text: str, tip: str, slot) -> QPushButton:
            btn = QPushButton(text)
            btn.setProperty("linkButton", True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
            return btn

        self.select_all_btn = link(
            "Select All", "Tick every playlist and mood the filter shows", self._select_all_items
        )
        self.select_none_btn = link(
            "Select None", "Untick every playlist and mood the filter shows", self._select_no_items
        )
        separator = QLabel("·")
        separator.setProperty("textRole", "muted")
        toolbar.addWidget(separator)
        self.expand_all_btn = link("Expand All", "Show all sub-playlists", self._expand_all_items)
        self.collapse_all_btn = link(
            "Collapse All", "Hide all sub-playlists", self._collapse_all_items
        )
        layout.addLayout(toolbar)

        self.sync_tree = SyncSelectionTree()
        self.sync_tree.itemChanged.connect(self._on_sync_item_changed)
        self.sync_tree.bulkCheckChanged.connect(self._on_sync_tree_bulk_changed)
        self.sync_tree.set_placeholder_text("Loading playlists and moods…")
        layout.addWidget(self.sync_tree, 1)

        hint = QLabel(
            "Right-click a playlist to select or clear it with all its sub-playlists."
        )
        hint.setProperty("textRole", "muted")
        hint.setObjectName("SyncHint")
        layout.addWidget(hint)

        return w

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(12)

        # ── Destination ─────────────────────────────────────────────────────
        destination_card = DetailCard("Destination")

        self.destination_mode = SegmentedControl(["Android device", "Folder"])
        if not mtp_available():
            self.destination_mode.button(ANDROID_MODE).setEnabled(False)
            self.destination_mode.setItemToolTip(
                ANDROID_MODE, "Install gvfs-backends to enable MTP device syncing"
            )
        self.destination_mode.currentIndexChanged.connect(self._on_destination_mode_changed)
        destination_card.body.addWidget(self.destination_mode, 0, Qt.AlignLeft)

        # Android section (kept under its old attribute name)
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
        self.link_device_btn.setToolTip(
            "Choose from connected Android devices"
            if mtp_available()
            else "Install gvfs-backends to enable MTP device syncing"
        )
        self.link_device_btn.clicked.connect(self._link_device)
        android.addWidget(self.link_device_btn, 0, 2)

        android.addWidget(self._field_label("Music folder"), 1, 0)
        self.music_path_edit = QComboBox()
        self.music_path_edit.setEditable(True)
        self.music_path_edit.setInsertPolicy(QComboBox.NoInsert)
        self.music_path_edit.addItems(COMMON_DEVICE_MUSIC_PATHS)
        self.music_path_edit.lineEdit().setPlaceholderText("/storage/emulated/0/Music")
        self.music_path_edit.setToolTip(
            "Pick a common location or type a custom relative path on the device"
        )
        self.music_path_edit.textActivated.connect(self._on_music_path_changed)
        self.music_path_edit.lineEdit().editingFinished.connect(self._on_music_path_changed)
        android.addWidget(self.music_path_edit, 1, 1, 1, 2)

        android.addWidget(self._field_label("Nickname"), 2, 0)
        self.device_nickname_edit = QLineEdit()
        self.device_nickname_edit.setPlaceholderText("e.g. My Pixel  (replaces the detected name)")
        self.device_nickname_edit.editingFinished.connect(self._on_nickname_changed)
        android.addWidget(self.device_nickname_edit, 2, 1, 1, 2)
        destination_card.body.addWidget(self.android_group)

        # Folder section
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
        destination_card.body.addWidget(self.folder_group)
        self.android_group.setVisible(False)

        layout.addWidget(destination_card)

        # ── Extra files on the destination ──────────────────────────────────
        cleanup_card = DetailCard("Files on the destination")
        self.cleanup_mode = SegmentedControl([label for label, *_ in CLEANUP_MODES])
        self.cleanup_mode.currentIndexChanged.connect(self._on_cleanup_mode_changed)
        cleanup_card.body.addWidget(self.cleanup_mode, 0, Qt.AlignLeft)
        self.cleanup_caption = QLabel(CLEANUP_MODES[KEEP_MODE][3])
        self.cleanup_caption.setObjectName("SyncOptionCaption")
        self.cleanup_caption.setWordWrap(True)
        cleanup_card.body.addWidget(self.cleanup_caption)
        layout.addWidget(cleanup_card)

        # ── Transcode lossless → MP3 ───────────────────────────────────────
        conversion_card = DetailCard("MP3 conversion")
        ffmpeg_ok = ffmpeg_available()
        self.transcode_mp3_check = QCheckBox("Convert lossless files to MP3")
        self.transcode_mp3_check.setEnabled(ffmpeg_ok)
        self.transcode_mp3_check.setToolTip(
            "FLAC / WAV / AIFF are re-encoded to CBR MP3 as they're copied to the device."
            if ffmpeg_ok
            else "Requires ffmpeg on your PATH  (e.g. sudo apt install ffmpeg)"
        )
        self.transcode_mp3_check.toggled.connect(self._on_option_changed)
        conversion_card.body.addWidget(self.transcode_mp3_check)

        conversion = QGridLayout()
        conversion.setContentsMargins(0, 4, 0, 0)
        conversion.setHorizontalSpacing(12)
        conversion.setVerticalSpacing(10)
        conversion.setColumnStretch(2, 1)

        conversion.addWidget(self._field_label("Bitrate"), 0, 0)
        # Kept as `bitrate_combo`: SegmentedControl speaks the QComboBox API.
        self.bitrate_combo = SegmentedControl(["320", "256", "192", "128"])
        self.bitrate_combo.setEnabled(False)
        self.bitrate_combo.currentTextChanged.connect(self._on_option_changed)
        conversion.addWidget(self.bitrate_combo, 0, 1)
        kbps = QLabel("kbps")
        kbps.setProperty("textRole", "muted")
        conversion.addWidget(kbps, 0, 2)

        # Cache size cap — global (the cache dir is shared across profiles), so
        # this is seeded from app_config here and never touched by
        # _load_profile_into_ui.
        conversion.addWidget(self._field_label("Cache limit"), 1, 0)
        self.cache_max_spin = QSpinBox()
        self.cache_max_spin.setRange(0, 102400)
        self.cache_max_spin.setSingleStep(256)
        self.cache_max_spin.setSuffix(" MB")
        self.cache_max_spin.setSpecialValueText("Unlimited")
        self.cache_max_spin.setValue(app_config.get_transcode_cache_max_mb())
        self.cache_max_spin.setToolTip(
            "After a sync that converts to MP3, the oldest unused conversions are "
            "deleted until the cache is back under this size. 0 = no limit."
        )
        self.cache_max_spin.valueChanged.connect(self._on_cache_max_changed)
        conversion.addWidget(self.cache_max_spin, 1, 1)
        self.clear_cache_btn = QPushButton("Clear MP3 cache")
        self.clear_cache_btn.clicked.connect(self._clear_transcode_cache)
        conversion.addWidget(self.clear_cache_btn, 1, 2, Qt.AlignLeft)
        conversion_card.body.addLayout(conversion)

        transcode_caption = QLabel(
            "Lossy files (MP3, AAC, M4A, OGG) copy unchanged. "
            "Your library's original files are never modified."
        )
        transcode_caption.setProperty("textRole", "muted")
        transcode_caption.setWordWrap(True)
        conversion_card.body.addWidget(transcode_caption)
        layout.addWidget(conversion_card)
        layout.addStretch()

        # Wrap in a scroll area so a short/narrow detail pane scrolls instead of
        # crunching the cards below their sizeHint.
        scroll = QScrollArea()
        scroll.setObjectName("SyncScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("textRole", "fieldLabel")
        return label

    def _build_log_tab(self) -> QWidget:
        self.activity = SyncActivityPanel()
        self.sync_log = self.activity.sync_log
        return self.activity

    # -- Bottom bar ----------------------------------------------------------

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("SyncBottomBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 16, 10)
        layout.setSpacing(12)

        # Idle, after a run: the result, with a jump to the Activity page.
        self.result_widget = QWidget()
        self.result_widget.setProperty("bgTransparent", True)
        result_layout = QHBoxLayout(self.result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)
        self.result_label = QLabel()
        self.result_label.setObjectName("SyncResultLabel")
        self.result_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        result_layout.addWidget(self.result_label, 1)
        self.result_details_btn = QPushButton("Details")
        self.result_details_btn.setProperty("linkButton", True)
        self.result_details_btn.setCursor(Qt.PointingHandCursor)
        self.result_details_btn.clicked.connect(lambda: self.tab_switch.setCurrentIndex(ACTIVITY_PAGE))
        self.result_details_btn.setVisible(False)
        result_layout.addWidget(self.result_details_btn)
        layout.addWidget(self.result_widget, 1)

        # Running: the current step and progress.
        self.current_action = QLabel()
        self.current_action.setObjectName("SyncCurrentAction")
        self.current_action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.current_action.setVisible(False)
        layout.addWidget(self.current_action, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("SyncProgressBar")
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedSize(220, 8)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # Idle: what "Sync now" will copy.
        self.track_count_label = QLabel()
        self.track_count_label.setObjectName("SyncSummaryLabel")
        layout.addWidget(self.track_count_label)

        self.cancel_sync_btn = QPushButton("Cancel")
        self.cancel_sync_btn.setVisible(False)
        self.cancel_sync_btn.clicked.connect(self._cancel_sync)
        layout.addWidget(self.cancel_sync_btn)

        self.sync_btn = QPushButton("Sync now  →")
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.setEnabled(False)
        self.sync_btn.setMinimumWidth(130)
        self.sync_btn.clicked.connect(self._start_sync)
        layout.addWidget(self.sync_btn)

        return bar

    # -----------------------------------------------------------------------
    # Pages, run state, result
    # -----------------------------------------------------------------------

    def _on_page_changed(self, index: int):
        self.tabs.setCurrentIndex(index)
        if index == ACTIVITY_PAGE:
            self._set_activity_attention(False)

    def _set_activity_attention(self, on: bool):
        """Dot the Activity segment while it has news the user hasn't looked at."""
        on = on and self.tabs.currentIndex() != ACTIVITY_PAGE
        self.tab_switch.setItemText(ACTIVITY_PAGE, "Activity  •" if on else "Activity")
        set_style_property(self.tab_switch.button(ACTIVITY_PAGE), "attention", on)

    def _set_sync_ui_state(self, idle: bool):
        self.sync_btn.setVisible(idle)
        self.track_count_label.setVisible(idle)
        self.result_widget.setVisible(idle)
        self.cancel_sync_btn.setVisible(not idle)
        self.current_action.setVisible(not idle)
        self.progress_bar.setVisible(not idle)
        self.add_profile_btn.setEnabled(idle)
        self.detect_btn.setEnabled(idle and mtp_available())
        # Deleting or renaming the profile mid-run would orphan the worker's view of it.
        self.profile_menu_btn.setEnabled(idle)
        if not idle:
            self._set_activity_attention(True)

    def _show_sync_result(self, text: str, tone: str):
        """Show a finished run's outcome in the bottom bar (tone: ok | warn | error)."""
        icon = {"ok": "✓", "warn": "⚠", "error": "✗"}.get(tone, "")
        self.result_label.setText(f"{icon}  {text}")
        self.result_label.setToolTip(text)
        set_style_property(self.result_label, "tone", tone)
        self.result_details_btn.setVisible(True)
        self._set_activity_attention(True)

    def _clear_sync_result(self):
        self.result_label.clear()
        self.result_details_btn.setVisible(False)

    # -----------------------------------------------------------------------
    # Card management
    # -----------------------------------------------------------------------

    def _rebuild_cards(self):
        """Clear and repopulate the sidebar card list from self.profiles."""
        for card in self.cards:
            card.setParent(None)
        self.cards.clear()
        self.selected_card = None

        # Drop the old trailing stretch; a fresh one is appended below.
        self.card_layout.takeAt(self.card_layout.count() - 1)

        for profile in self.profiles:
            card = DeviceCard(profile, self._on_card_clicked, self.card_container)
            self.cards.append(card)
            self.card_layout.addWidget(card)

        self.card_layout.addStretch()

        # Restore connection badges — background scan, result lands in
        # _on_mtp_devices_listed
        self._refresh_mtp_devices()

    def _on_card_clicked(self, card: DeviceCard):
        """Handle a card being clicked — select it and load its profile."""
        if self.current_profile is not None:
            self._save_current_profile_selections()

        if self.selected_card:
            self.selected_card.set_selected(False)

        card.set_selected(True)
        # The bottom bar's last result belongs to the profile it ran for.
        if card is not self.selected_card and not self._scan_in_progress(self.sync_worker):
            self._clear_sync_result()
        self.selected_card = card
        self.current_profile = card.profile

        self._load_profile_into_ui()
        self._update_sync_button_state()

    def _find_card_for_profile(self, profile: SyncProfile) -> DeviceCard | None:
        for card in self.cards:
            if card.profile is profile:
                return card
        return None

    def _refresh_current_card(self):
        card = self._find_card_for_profile(self.current_profile) if self.current_profile else None
        if card:
            card.update_profile(self.current_profile)

    def _update_selected_items(self):
        super()._update_selected_items()
        card = self._find_card_for_profile(self.current_profile) if self.current_profile else None
        if card:
            card.set_track_total(self._selection_totals[0])

    def _save_current_profile_selections(self):
        super()._save_current_profile_selections()
        self._refresh_current_card()

    # -----------------------------------------------------------------------
    # Profile CRUD
    # -----------------------------------------------------------------------

    def _load_profiles(self):
        self.profiles = self.profile_store.load()
        self._rebuild_cards()
        if self.profiles:
            self._on_card_clicked(self.cards[0])
        else:
            self._show_placeholder()

    def _add_and_select_profile(self, profile: SyncProfile):
        self.profiles.append(profile)
        self.profile_store.save(self.profiles)
        self._rebuild_cards()
        logger.info(f"Created new sync profile: {profile.name}")
        new_card = self._find_card_for_profile(profile)
        if new_card:
            self._on_card_clicked(new_card)

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        self._add_and_select_profile(
            SyncProfile(name=name.strip(), path="", music_path=MtpManager.DEFAULT_MUSIC_PATH)
        )
        self.tab_switch.setCurrentIndex(OPTIONS_PAGE)  # a blank profile needs a destination next

    def _new_folder_profile(self):
        """Empty-state shortcut: pick a folder and make a profile for it."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Sync Destination Folder", "", QFileDialog.ShowDirsOnly
        )
        if not folder:
            return
        name = Path(folder).name or folder
        self._add_and_select_profile(
            SyncProfile(name=name, path=folder, music_path=MtpManager.DEFAULT_MUSIC_PATH)
        )

    def _rename_profile(self):
        if not self.current_profile:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Profile", "Profile name:", text=self.current_profile.name
        )
        new_name = name.strip()
        if not ok or not new_name or new_name == self.current_profile.name:
            return
        self.current_profile.name = new_name
        self.profile_store.save(self.profiles)
        self._refresh_current_card()
        self._refresh_header()

    def _delete_profile(self):
        if not self.current_profile:
            return
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{self.current_profile.name}'?\n\n"
            "This only removes the profile — no files are deleted.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        deleted_name = self.current_profile.name
        self.profiles.remove(self.current_profile)
        self.current_profile = None
        self.profile_store.save(self.profiles)
        self._rebuild_cards()
        logger.info(f"Deleted sync profile: {deleted_name}")

        if self.profiles:
            self._on_card_clicked(self.cards[0])
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        self.detail_stack.setCurrentWidget(self.placeholder)
        self._update_sync_button_state()

    # -----------------------------------------------------------------------
    # Loading a profile into the UI
    # -----------------------------------------------------------------------

    def _load_profile_into_ui(self):
        """Populate all UI controls from self.current_profile."""
        if not self.current_profile:
            self._show_placeholder()
            return

        self.detail_stack.setCurrentWidget(self.profile_page)
        p = self.current_profile

        # Options page
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
        self.bitrate_combo.setCurrentText((p.transcode_bitrate or "320k").rstrip("k"))
        self.bitrate_combo.setEnabled(ffmpeg_ok and p.transcode_to_mp3)
        self.bitrate_combo.blockSignals(False)

        self._update_cache_button_label()
        self._refresh_header()

        # Music page
        self._apply_profile_selection()

    def _sync_destination_mode_control(self):
        """Point the Android/Folder switch (and its sections) at the profile's real mode."""
        is_mtp = bool(self.current_profile and self.current_profile.is_mtp)
        self.destination_mode.blockSignals(True)
        self.destination_mode.setCurrentIndex(ANDROID_MODE if is_mtp else FOLDER_MODE)
        self.destination_mode.blockSignals(False)
        self._show_destination_section(is_mtp)

    def _show_destination_section(self, android: bool):
        self.android_group.setVisible(android)
        self.folder_group.setVisible(not android)

    def _refresh_header(self):
        """Update the profile header and the Options page's linked-device row."""
        p = self.current_profile
        if not p:
            return
        self.profile_title.setText(p.name)
        self.profile_icon.setText(DEVICE_GLYPH if p.is_mtp else FOLDER_GLYPH)

        if p.device_uri:
            # Friendly name comes from the last background MTP scan
            # (_known_mtp_devices) — never enumerate devices on the GUI thread.
            match = next((d for d in self._known_mtp_devices if d.uri == p.device_uri), None)
            name = p.device_name or (match.display_name if match else p.device_uri)
            if match:
                self.device_label.setText("● Connected")
                set_style_property(self.device_label, "linkState", "connected")
            else:
                self.device_label.setText("○ Not connected")
                set_style_property(self.device_label, "linkState", "idle")
            destination = f"{name}  ·  {p.music_path or MtpManager.DEFAULT_MUSIC_PATH}"
            self.linked_device_label.setText(match.display_name if match else f"{name}  (not connected)")
            self.change_destination_btn.setText("Change device…")
            if not self._scan_in_progress(self._link_worker):
                self.link_device_btn.setText("Change device…")
        else:
            self.device_label.setText("Folder")
            set_style_property(self.device_label, "linkState", "folder")
            destination = p.path or "No folder chosen yet"
            self.linked_device_label.setText("No device linked")
            self.change_destination_btn.setText("Change folder…" if p.path else "Choose folder…")
            if not self._scan_in_progress(self._link_worker):
                self.link_device_btn.setText("Link device…")

        metrics = self.destination_label.fontMetrics()
        width = max(self.destination_label.width(), 200)
        self.destination_label.setText(metrics.elidedText(destination, Qt.ElideMiddle, width))
        self.destination_label.setToolTip(destination)

    @staticmethod
    def _scan_in_progress(worker) -> bool:
        """True while `worker` (an MtpListWorker or SyncWorker thread) is running."""
        return worker is not None and worker.isRunning()

    # -----------------------------------------------------------------------
    # Options page handlers
    # -----------------------------------------------------------------------

    def _change_destination(self):
        if not self.current_profile:
            return
        if self.current_profile.is_mtp:
            self._link_device()
        else:
            self._browse_folder()

    def _on_destination_mode_changed(self, index: int):
        if not self.current_profile:
            return
        if index == FOLDER_MODE:
            self._show_destination_section(False)
            if self.current_profile.is_mtp:
                self._unlink_device()
        elif not self.current_profile.is_mtp:
            # Android only becomes the real mode once a device is linked;
            # _on_link_devices_listed reverts the switch if that doesn't happen.
            self._show_destination_section(True)
            self._link_device()

    def _on_nickname_changed(self):
        if not self.current_profile:
            return
        nickname = self.device_nickname_edit.text().strip()
        self.current_profile.device_name = nickname
        self._refresh_current_card()
        self.profile_store.save(self.profiles)
        self._refresh_header()

    def _on_music_path_changed(self, *_):
        if not self.current_profile:
            return
        new_path = self.music_path_edit.currentText().strip()
        if new_path == self.current_profile.music_path:
            return
        self.current_profile.music_path = new_path
        self.profile_store.save(self.profiles)
        self._refresh_current_card()
        self._refresh_header()

    def _on_cleanup_mode_changed(self, index: int):
        self._update_cleanup_caption(index)
        if not self.current_profile:
            return
        _label, clear, prune, _caption = CLEANUP_MODES[index]
        self.current_profile.clear_before_sync = clear
        self.current_profile.prune_untracked = prune
        self.profile_store.save(self.profiles)

    def _update_cleanup_caption(self, index: int):
        self.cleanup_caption.setText(CLEANUP_MODES[index][3])
        set_style_property(self.cleanup_caption, "tone", "warn" if index == WIPE_MODE else None)

    def _on_option_changed(self):
        if not self.current_profile:
            return
        self.current_profile.transcode_to_mp3 = self.transcode_mp3_check.isChecked()
        self.current_profile.transcode_bitrate = f"{self.bitrate_combo.currentText()}k"
        self.bitrate_combo.setEnabled(
            self.transcode_mp3_check.isEnabled() and self.transcode_mp3_check.isChecked()
        )
        self.profile_store.save(self.profiles)
        # The selection summary shows a post-conversion size estimate while
        # transcoding is on, so it has to re-render when the toggle or the
        # bitrate changes.
        self._update_selected_items()

    def _update_cache_button_label(self):
        """Show the current transcode-cache size on the Clear button."""
        size_mb = TranscodeCache().size_bytes() / (1024 * 1024)
        self.clear_cache_btn.setText(f"Clear MP3 cache ({size_mb:.0f} MB)")
        self.clear_cache_btn.setEnabled(size_mb > 0)

    def _on_cache_max_changed(self, value_mb: int):
        """Persist the transcode-cache size cap (global, not per-profile)."""
        app_config.set_transcode_cache_max_mb(value_mb)
        app_config.save()

    def _clear_transcode_cache(self):
        cache = TranscodeCache()
        size_mb = cache.size_bytes() / (1024 * 1024)
        reply = QMessageBox.question(
            self,
            "Clear MP3 cache",
            f"Delete {size_mb:.0f} MB of cached MP3 conversions?\n\n"
            "They are re-created automatically on the next sync that needs them.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        removed = cache.clear()
        logger.info(f"Cleared transcode cache: {removed} file(s)")
        show_status_message(self, f"Cleared {removed} cached MP3 file(s).")
        self._update_cache_button_label()

    def _browse_folder(self):
        if not self.current_profile:
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Sync Destination Folder",
            self.current_profile.path or "",
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            logger.info(
                f"Sync destination folder set to '{folder}' "
                f"for profile '{self.current_profile.name}'"
            )
            self.current_profile.path = folder
            self.folder_label.setText(folder)
            set_style_property(self.folder_label, "textRole", None)
            self._refresh_current_card()
            self.profile_store.save(self.profiles)
            self._refresh_header()
            self._update_sync_button_state()

    def _link_device(self):
        """Scan for MTP devices off the GUI thread, then offer a picker."""
        if not mtp_available() or self._scan_in_progress(self._link_worker):
            return
        self.link_device_btn.setText("Scanning…")
        self.link_device_btn.setEnabled(False)
        self.change_destination_btn.setEnabled(False)
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_link_devices_listed)
        self._link_worker = worker
        worker.start()

    def _on_link_devices_listed(self, devices: list):
        self.link_device_btn.setEnabled(True)
        self.change_destination_btn.setEnabled(True)
        self._known_mtp_devices = devices
        self._update_connection_badges({d.uri for d in devices})
        if not self.current_profile:
            return
        if not devices:
            show_status_message(
                self,
                "No Devices Found: No Android devices were detected. Make sure your "
                "phone is connected via USB and set to File Transfer mode (pull down "
                "the notification shade and tap the USB notification).",
            )
            self._sync_destination_mode_control()
            self._refresh_header()
            return

        if len(devices) == 1:
            chosen = devices[0]
        else:
            options = [d.display_name for d in devices]
            choice, ok = QInputDialog.getItem(self, "Link Device", "Select device:", options, 0, False)
            if not ok:
                self._sync_destination_mode_control()
                self._refresh_header()
                return
            chosen = devices[options.index(choice)]

        self.current_profile.device_uri = chosen.uri
        self.current_profile.device_name = chosen.short_name
        self.profile_store.save(self.profiles)
        logger.info(
            f"Linked device '{chosen.display_name}' to profile '{self.current_profile.name}'"
        )
        show_status_message(self, f"Linked {chosen.display_name}.")

        self.device_nickname_edit.setText(chosen.short_name)
        self._sync_destination_mode_control()
        self._refresh_header()
        self._refresh_current_card()
        self._update_sync_button_state()

    def _unlink_device(self):
        if not self.current_profile:
            return
        logger.info(f"Unlinked device from profile '{self.current_profile.name}'")
        self.current_profile.device_uri = ""
        self.current_profile.device_name = ""
        self.profile_store.save(self.profiles)
        self.device_nickname_edit.clear()
        self._sync_destination_mode_control()
        self._refresh_header()
        self._refresh_current_card()
        self._update_sync_button_state()

    # -----------------------------------------------------------------------
    # MTP device detection
    # -----------------------------------------------------------------------

    def _detect_devices(self):
        """Scan for MTP devices (off the GUI thread) and offer profiles for unknown ones."""
        if not mtp_available() or self._scan_in_progress(self._detect_worker):
            return
        self.detect_btn.setText("⟳ Scanning…")
        self.detect_btn.setEnabled(False)
        self.empty_detect_btn.setText("Scanning…")
        self.empty_detect_btn.setEnabled(False)
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_detect_devices_listed)
        self._detect_worker = worker
        worker.start()

    def _on_detect_devices_listed(self, devices: list):
        self._known_mtp_devices = devices
        logger.info(f"MTP device scan found {len(devices)} device(s)")
        self.detect_btn.setText("⟳ Detect")
        self.detect_btn.setEnabled(True)
        self.empty_detect_btn.setText("Detect Android device")
        self.empty_detect_btn.setEnabled(True)

        if not devices:
            show_status_message(
                self,
                "No Devices Found: No devices detected via USB. Make sure your "
                "phone is connected and set to File Transfer mode (pull down the "
                "notification shade and tap the USB notification).",
            )
            return

        # Find devices not yet linked to any profile
        known_uris = {p.device_uri for p in self.profiles if p.device_uri}
        new_devices = [d for d in devices if d.uri not in known_uris]

        # Update connection badges regardless
        self._update_connection_badges({d.uri for d in devices})
        self._refresh_header()

        if not new_devices:
            show_status_message(
                self, f"{len(devices)} device(s) connected — all already have profiles."
            )
            return

        # Offer to create a profile for each new device
        for device in new_devices:
            reply = QMessageBox.question(
                self,
                "New Device Found",
                f"Found: {device.display_name}\n\nCreate a sync profile for this device?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._add_and_select_profile(
                    SyncProfile(
                        name=device.short_name,
                        path="",
                        device_uri=device.uri,
                        device_name=device.short_name,
                        music_path=MtpManager.DEFAULT_MUSIC_PATH,
                    )
                )

    def _refresh_mtp_devices(self):
        """Enumerate connected MTP devices on a background thread; badges and
        the header update when the result arrives.

        Never calls `gio` on the GUI thread — a `gio` enumeration against a
        wedged MTP backend blocks its caller with no bounded recovery, and
        this runs on view open and every 5 s from _mtp_poll_timer.
        """
        if not mtp_available():
            return
        if self._scan_in_progress(self._mtp_list_worker):
            return  # a scan is already in flight — don't stack gio calls
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_mtp_devices_listed)
        self._mtp_list_worker = worker
        worker.start()

    def _on_mtp_devices_listed(self, devices: list):
        """Apply a background MTP scan result to the sidebar and the header."""
        self._known_mtp_devices = devices
        self._update_connection_badges({d.uri for d in devices})
        self._refresh_header()

    def _update_connection_badges(self, connected_uris: set):
        """Flip each card's USB badge to match `connected_uris`."""
        for card in self.cards:
            if card.profile.device_uri:
                card.set_connected(card.profile.device_uri in connected_uris)
