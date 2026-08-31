# sync_view.py

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from src.common.style_utils import set_style_property
from src.core.logger_config import logger
from src.core.status_utility import StatusManager, show_status_message
from src.db.db_helpers import Session
from src.display.display_settings import apply_scaled_style
from src.sync.device_card import DeviceCard
from src.sync.mtp_list_worker import MtpListWorker
from src.sync.mtp_manager import MtpDevice, MtpManager, mtp_available
from src.sync.sync_execution_mixin import SyncExecutionMixin
from src.sync.sync_manager import SyncManager
from src.sync.sync_profile import SyncProfile, SyncProfileStore
from src.sync.sync_selection_mixin import SyncSelectionMixin
from src.sync.sync_worker import SyncWorker

# ---------------------------------------------------------------------------
# SyncView — main view
# ---------------------------------------------------------------------------


class SyncView(SyncSelectionMixin, SyncExecutionMixin, QWidget):
    """
    Two-panel sync view.

    Left panel  — scrollable list of DeviceCards (one per profile) with
                  Add/Detect buttons at the bottom.
    Right panel — tabbed detail area for the selected profile:
                  • Playlists   — checklist of playlists to sync
                  • Settings    — device path, music path, options
                  • Log         — live sync progress output
    Bottom bar  — progress bar + Start Sync / Cancel always visible.
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

        # MTP device enumeration runs on a throwaway thread (MtpListWorker):
        # `gio` can block its caller indefinitely against a wedged device, and
        # this view scans on open and every 5 s. _known_mtp_devices caches the
        # most recent successful scan for _refresh_device_label to read.
        self._known_mtp_devices: list[MtpDevice] = []
        self._mtp_list_worker: MtpListWorker | None = None

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

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Body: splitter with left sidebar + right detail ──────────────────
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)

        self.splitter.addWidget(self._build_sidebar())
        self.splitter.addWidget(self._build_detail_panel())
        self.splitter.setSizes([260, 700])

        root.addWidget(self.splitter, 1)

        # ── Bottom bar ───────────────────────────────────────────────────────
        root.addWidget(self._build_bottom_bar())

    # -- Sidebar -------------------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("SyncSidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(320)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(8)

        # Section label
        section_lbl = QLabel("DEVICES & PROFILES")
        section_lbl.setObjectName("SyncSectionLabel")
        layout.addWidget(section_lbl)

        # Scrollable card list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;")

        self.card_container = QWidget()
        self.card_container.setStyleSheet("background:transparent;")
        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(6)
        self.card_layout.addStretch()

        scroll.setWidget(self.card_container)
        layout.addWidget(scroll, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.add_profile_btn = QPushButton("+ New")
        self.add_profile_btn.setToolTip("Create a new sync profile")
        self.add_profile_btn.clicked.connect(self._new_profile)
        btn_row.addWidget(self.add_profile_btn)

        self.detect_btn = QPushButton("⟳ Detect")
        self.detect_btn.setToolTip(
            "Scan for connected Android devices via USB"
            if mtp_available()
            else "Install gvfs-backends (sudo apt install gvfs-backends) to enable device detection"
        )
        self.detect_btn.setEnabled(mtp_available())
        self.detect_btn.clicked.connect(self._detect_devices)
        btn_row.addWidget(self.detect_btn)

        self.delete_sidebar_btn = QPushButton("🗑")
        self.delete_sidebar_btn.setToolTip("Delete selected profile")
        self.delete_sidebar_btn.setEnabled(False)
        self.delete_sidebar_btn.setFixedWidth(32)
        self.delete_sidebar_btn.clicked.connect(self._delete_profile)
        btn_row.addWidget(self.delete_sidebar_btn)

        layout.addLayout(btn_row)

        return sidebar

    # -- Detail panel --------------------------------------------------------

    def _build_detail_panel(self) -> QWidget:
        self.detail_panel = QWidget()
        self.detail_panel.setObjectName("SyncDetail")

        layout = QVBoxLayout(self.detail_panel)
        layout.setContentsMargins(16, 12, 16, 0)
        layout.setSpacing(0)

        # Placeholder shown when no profile is selected
        self.placeholder = QLabel("← Select a profile or create a new one")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("SyncPlaceholder")
        layout.addWidget(self.placeholder)

        # Tab widget (hidden until a profile is selected)
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        self.tabs.addTab(self._build_selection_tab(), "Playlists && Moods")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.tabs.addTab(self._build_log_tab(), "Log")
        layout.addWidget(self.tabs, 1)

        return self.detail_panel

    def _build_selection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)

        # Toolbar: select all / none + track count label
        toolbar = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setFixedWidth(90)
        self.select_all_btn.clicked.connect(self._select_all_items)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setFixedWidth(90)
        self.select_none_btn.clicked.connect(self._select_no_items)

        self.track_count_label = QLabel("")
        apply_scaled_style(self.track_count_label, "color:#555e7a; font-size:11px;")

        toolbar.addWidget(self.select_all_btn)
        toolbar.addWidget(self.select_none_btn)
        toolbar.addStretch()
        toolbar.addWidget(self.track_count_label)
        layout.addLayout(toolbar)

        # Playlists + Moods checklist tree
        self.sync_tree = QTreeWidget()
        self.sync_tree.setHeaderHidden(True)
        self.sync_tree.setColumnCount(1)
        self.sync_tree.setAlternatingRowColors(False)
        self.sync_tree.itemChanged.connect(self._on_sync_item_changed)
        layout.addWidget(self.sync_tree, 1)

        return w

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(16)

        # ── Profile identity ────────────────────────────────────────────────
        identity_group = QGroupBox("Profile")
        identity_layout = QVBoxLayout(identity_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.profile_name_edit = QLineEdit()
        self.profile_name_edit.setPlaceholderText("Profile name…")
        self.profile_name_edit.editingFinished.connect(self._on_profile_name_changed)
        name_row.addWidget(self.profile_name_edit, 1)
        identity_layout.addLayout(name_row)

        nick_row = QHBoxLayout()
        nick_row.addWidget(QLabel("Device nickname:"))
        self.device_nickname_edit = QLineEdit()
        self.device_nickname_edit.setPlaceholderText(
            "e.g. My Pixel  (overrides auto-detected name)"
        )
        self.device_nickname_edit.editingFinished.connect(self._on_nickname_changed)
        nick_row.addWidget(self.device_nickname_edit, 1)
        identity_layout.addLayout(nick_row)

        layout.addWidget(identity_group)

        # ── Android device ──────────────────────────────────────────────────
        self.android_group = QGroupBox("Android Device  (USB)")
        android_layout = QVBoxLayout(self.android_group)

        # Connected device indicator
        device_row = QHBoxLayout()
        self.device_label = QLabel("No device linked")
        self.device_label.setProperty("linkState", "idle")
        device_row.addWidget(self.device_label, 1)

        self.link_device_btn = QPushButton("Link Device…")
        self.link_device_btn.setEnabled(mtp_available())
        self.link_device_btn.setToolTip(
            "Choose from connected Android devices"
            if mtp_available()
            else "Install gvfs-backends to enable MTP device syncing"
        )
        self.link_device_btn.clicked.connect(self._link_device)
        device_row.addWidget(self.link_device_btn)

        self.unlink_device_btn = QPushButton("Unlink")
        self.unlink_device_btn.clicked.connect(self._unlink_device)
        self.unlink_device_btn.setVisible(False)
        device_row.addWidget(self.unlink_device_btn)

        android_layout.addLayout(device_row)

        # Music path on device
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Music folder on device:"))
        self.music_path_edit = QLineEdit()
        self.music_path_edit.setPlaceholderText("/storage/emulated/0/Music")
        self.music_path_edit.editingFinished.connect(self._on_music_path_changed)
        path_row.addWidget(self.music_path_edit, 1)
        android_layout.addLayout(path_row)

        layout.addWidget(self.android_group)

        # ── Folder sync ─────────────────────────────────────────────────────
        self.folder_group = QGroupBox("Folder Sync  (fallback / non-Android)")
        folder_layout = QHBoxLayout(self.folder_group)

        self.folder_label = QLabel("No folder set")
        self.folder_label.setProperty("textRole", "muted")
        self.folder_label.setWordWrap(True)
        folder_layout.addWidget(self.folder_label, 1)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(self.browse_btn)

        layout.addWidget(self.folder_group)

        # ── Sync options ────────────────────────────────────────────────────
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)

        self.clear_before_sync_check = QCheckBox(
            "Clear destination before syncing  (removes existing music and playlist folders first)"
        )
        self.clear_before_sync_check.toggled.connect(self._on_option_changed)
        options_layout.addWidget(self.clear_before_sync_check)

        layout.addWidget(options_group)
        layout.addStretch()

        return w

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(6)

        self.current_action = QLabel("Ready to sync")
        self.current_action.setStyleSheet("color:#8599ea; font-weight:bold;")
        layout.addWidget(self.current_action)

        self.sync_log = QTextEdit()
        self.sync_log.setReadOnly(True)
        self.sync_log.setFont(QFont("Courier", 9))
        self.sync_log.setObjectName("SyncLogView")
        layout.addWidget(self.sync_log, 1)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(self.sync_log.clear)
        layout.addWidget(clear_btn, 0, Qt.AlignRight)

        return w

    # -- Bottom bar ----------------------------------------------------------

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("SyncBottomBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("SyncProgressBar")
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar, 1)

        self.cancel_sync_btn = QPushButton("Cancel")
        self.cancel_sync_btn.setVisible(False)
        self.cancel_sync_btn.clicked.connect(self._cancel_sync)
        layout.addWidget(self.cancel_sync_btn)

        self.sync_btn = QPushButton("Start Sync  →")
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.setEnabled(False)
        self.sync_btn.setMinimumWidth(120)
        self.sync_btn.clicked.connect(self._start_sync)
        layout.addWidget(self.sync_btn)

        return bar

    # -----------------------------------------------------------------------
    # Card management
    # -----------------------------------------------------------------------

    def _rebuild_cards(self):
        """Clear and repopulate the sidebar card list from self.profiles."""
        # Remove old cards
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

        # Deselect old card
        if self.selected_card:
            self.selected_card.set_selected(False)

        card.set_selected(True)
        self.selected_card = card
        self.current_profile = card.profile
        self.delete_sidebar_btn.setEnabled(True)

        self._load_profile_into_ui()
        self._update_sync_button_state()

    def _find_card_for_profile(self, profile: SyncProfile) -> DeviceCard | None:
        for card in self.cards:
            if card.profile is profile:
                return card
        return None

    # -----------------------------------------------------------------------
    # Profile CRUD
    # -----------------------------------------------------------------------

    def _load_profiles(self):
        self.profiles = self.profile_store.load()
        self._rebuild_cards()
        if self.profiles:
            # Auto-select first profile
            self._on_card_clicked(self.cards[0])

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        profile = SyncProfile(name=name.strip(), path="", music_path=MtpManager.DEFAULT_MUSIC_PATH)
        self.profiles.append(profile)
        self.profile_store.save(self.profiles)
        self._rebuild_cards()
        logger.info(f"Created new sync profile: {profile.name}")
        # Select the new card
        new_card = self.cards[-1]
        self._on_card_clicked(new_card)

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
        self.placeholder.setVisible(True)
        self.tabs.setVisible(False)
        self.delete_sidebar_btn.setEnabled(False)
        self._update_sync_button_state()

    # -----------------------------------------------------------------------
    # Loading a profile into the UI
    # -----------------------------------------------------------------------

    def _load_profile_into_ui(self):
        """Populate all UI controls from self.current_profile."""
        if not self.current_profile:
            self._show_placeholder()
            return

        self.placeholder.setVisible(False)
        self.tabs.setVisible(True)

        p = self.current_profile

        # Settings tab
        self.profile_name_edit.blockSignals(True)
        self.profile_name_edit.setText(p.name)
        self.profile_name_edit.blockSignals(False)

        self.device_nickname_edit.blockSignals(True)
        self.device_nickname_edit.setText(p.device_name)
        self.device_nickname_edit.blockSignals(False)

        self.music_path_edit.blockSignals(True)
        self.music_path_edit.setText(p.music_path)
        self.music_path_edit.blockSignals(False)

        self.folder_label.setText(p.path or "No folder set")
        set_style_property(self.folder_label, "textRole", None if p.path else "muted")

        self.clear_before_sync_check.blockSignals(True)
        self.clear_before_sync_check.setChecked(p.clear_before_sync)
        self.clear_before_sync_check.blockSignals(False)

        self._refresh_device_label()

        # Playlists & Moods tab
        self._apply_profile_selection()

    def _refresh_device_label(self):
        """Update the linked device label in the Settings tab."""
        if not self.current_profile:
            return
        if self.current_profile.device_uri:
            # Friendly name comes from the last background MTP scan
            # (_known_mtp_devices) — never enumerate devices on the GUI thread.
            match = next(
                (d for d in self._known_mtp_devices if d.uri == self.current_profile.device_uri),
                None,
            )
            if match:
                self.device_label.setText(match.display_name)
                set_style_property(self.device_label, "linkState", "connected")
            else:
                name = self.current_profile.device_name or self.current_profile.device_uri
                self.device_label.setText(f"{name}  (not connected)")
                set_style_property(self.device_label, "linkState", "idle")
            self.unlink_device_btn.setVisible(True)
        else:
            self.device_label.setText("No device linked — using folder sync")
            set_style_property(self.device_label, "linkState", "idle")
            self.unlink_device_btn.setVisible(False)

    # -----------------------------------------------------------------------
    # Settings tab handlers
    # -----------------------------------------------------------------------

    def _on_profile_name_changed(self):
        if not self.current_profile:
            return
        new_name = self.profile_name_edit.text().strip()
        if new_name and new_name != self.current_profile.name:
            self.current_profile.name = new_name
            card = self._find_card_for_profile(self.current_profile)
            if card:
                card.update_profile(self.current_profile)
            self.profile_store.save(self.profiles)

    def _on_nickname_changed(self):
        if not self.current_profile:
            return
        nickname = self.device_nickname_edit.text().strip()
        self.current_profile.device_name = nickname
        card = self._find_card_for_profile(self.current_profile)
        if card:
            card.update_profile(self.current_profile)
        self.profile_store.save(self.profiles)
        self._refresh_device_label()

    def _on_music_path_changed(self):
        if not self.current_profile:
            return
        self.current_profile.music_path = self.music_path_edit.text().strip()
        self.profile_store.save(self.profiles)

    def _on_option_changed(self):
        if not self.current_profile:
            return
        self.current_profile.clear_before_sync = self.clear_before_sync_check.isChecked()
        self.profile_store.save(self.profiles)

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
            card = self._find_card_for_profile(self.current_profile)
            if card:
                card.update_profile(self.current_profile)
            self.profile_store.save(self.profiles)
            self._update_sync_button_state()

    def _link_device(self):
        """Show a picker of currently connected MTP devices."""
        devices = self.mtp_manager.list_devices()
        self._known_mtp_devices = devices
        if not devices:
            show_status_message(
                self,
                "No Devices Found: No Android devices were detected. Make sure your "
                "phone is connected via USB and set to File Transfer mode (pull down "
                "the notification shade and tap the USB notification).",
            )
            return

        options = [d.display_name for d in devices]
        choice, ok = QInputDialog.getItem(self, "Link Device", "Select device:", options, 0, False)
        if not ok:
            return

        chosen = devices[options.index(choice)]
        self.current_profile.device_uri = chosen.uri
        self.current_profile.device_name = chosen.short_name
        self.profile_store.save(self.profiles)
        logger.info(
            f"Linked device '{chosen.display_name}' to profile '{self.current_profile.name}'"
        )

        self._refresh_device_label()
        card = self._find_card_for_profile(self.current_profile)
        if card:
            card.update_profile(self.current_profile)
            card._refresh_display()
        self._update_sync_button_state()

    def _unlink_device(self):
        if not self.current_profile:
            return
        logger.info(f"Unlinked device from profile '{self.current_profile.name}'")
        self.current_profile.device_uri = ""
        self.current_profile.device_name = ""
        self.profile_store.save(self.profiles)
        self._refresh_device_label()
        card = self._find_card_for_profile(self.current_profile)
        if card:
            card.update_profile(self.current_profile)
        self._update_sync_button_state()

    # -----------------------------------------------------------------------
    # MTP device detection
    # -----------------------------------------------------------------------

    def _detect_devices(self):
        """Scan for MTP devices and offer to create profiles for unknown ones."""
        if not mtp_available():
            return

        self.detect_btn.setText("⟳ Scanning…")
        self.detect_btn.setEnabled(False)
        QTimer.singleShot(100, self._do_detect)

    def _do_detect(self):
        devices = self.mtp_manager.list_devices()
        self._known_mtp_devices = devices
        logger.info(f"MTP device scan found {len(devices)} device(s)")
        self.detect_btn.setText("⟳ Detect")
        self.detect_btn.setEnabled(True)

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
                profile = SyncProfile(
                    name=device.short_name,
                    path="",
                    device_uri=device.uri,
                    device_name=device.short_name,
                    music_path=MtpManager.DEFAULT_MUSIC_PATH,
                )
                self.profiles.append(profile)
                self.profile_store.save(self.profiles)
                self._rebuild_cards()
                new_card = self._find_card_for_profile(profile)
                if new_card:
                    self._on_card_clicked(new_card)

    def _refresh_mtp_devices(self):
        """Enumerate connected MTP devices on a background thread; badges and
        the device label update when the result arrives.

        Never calls `gio` on the GUI thread — a `gio` enumeration against a
        wedged MTP backend blocks its caller with no bounded recovery, and
        this runs on view open and every 5 s from _mtp_poll_timer.
        """
        if not mtp_available():
            return
        if self._mtp_list_worker is not None and self._mtp_list_worker.isRunning():
            return  # a scan is already in flight — don't stack gio calls
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_mtp_devices_listed)
        self._mtp_list_worker = worker
        worker.start()

    def _on_mtp_devices_listed(self, devices: list):
        """Apply a background MTP scan result to the sidebar and Settings tab."""
        self._known_mtp_devices = devices
        self._update_connection_badges({d.uri for d in devices})
        self._refresh_device_label()

    def _update_connection_badges(self, connected_uris: set):
        """Flip each card's USB badge to match `connected_uris`."""
        for card in self.cards:
            if card.profile.device_uri:
                card.set_connected(card.profile.device_uri in connected_uris)
