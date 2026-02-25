# sync_view.py
from typing import Dict, List, Optional

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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db_helpers import Session
from logger_config import logger
from status_utility import StatusManager
from sync_utility import (
    MtpDevice,
    MtpManager,
    SyncManager,
    SyncProfile,
    SyncProfileStore,
    SyncWorker,
    mtp_available,
)

# ---------------------------------------------------------------------------
# DeviceCard — a single profile card in the left sidebar
# ---------------------------------------------------------------------------


class DeviceCard(QFrame):
    """
    A clickable card representing one sync profile in the sidebar.

    Shows the profile name, sync type (Android USB / Folder), and
    a live connection badge when an Android device is linked.

    on_click is a callable that receives this card — avoids fragile
    parent() chains through scroll area viewports.
    """

    def __init__(self, profile: SyncProfile, on_click, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._on_click = on_click
        self.setObjectName("DeviceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._selected = False
        self._connected = False
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        # Top row: name + badge
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.name_label = QLabel(self.profile.name)
        self.name_label.setObjectName("CardTitle")
        font = QFont()
        font.setBold(True)
        self.name_label.setFont(font)
        top_row.addWidget(self.name_label, 1)

        self.badge = QLabel()
        self.badge.setFixedHeight(18)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setObjectName("CardBadge")
        top_row.addWidget(self.badge)

        layout.addLayout(top_row)

        # Subtitle: path or device info
        self.sub_label = QLabel()
        self.sub_label.setObjectName("CardSub")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)

        self._refresh_display()

    def _refresh_display(self):
        """Update text and badge to reflect current profile state."""
        self.name_label.setText(self.profile.name)

        if self.profile.is_mtp:
            if self._connected:
                self.badge.setText("● USB")
                self.badge.setStyleSheet(
                    "background:#1a3a1a; color:#99EA85; border-radius:9px;"
                    "padding:0 7px; font-size:10px; font-weight:bold;"
                )
            else:
                self.badge.setText("○ USB")
                self.badge.setStyleSheet(
                    "background:#2a2c3e; color:#555e7a; border-radius:9px;"
                    "padding:0 7px; font-size:10px; font-weight:bold;"
                )
            self.sub_label.setText(self.profile.music_path or "No path set")
        else:
            self.badge.setText("📁 Folder")
            self.badge.setStyleSheet(
                "background:#1a1b2a; color:#8599ea; border-radius:9px;"
                "padding:0 7px; font-size:10px; font-weight:bold;"
            )
            self.sub_label.setText(self.profile.path or "No folder set")

        self.sub_label.setStyleSheet("color:#555e7a; font-size:11px;")

    def set_selected(self, selected: bool):
        self._selected = selected
        self.setStyleSheet(
            "DeviceCard { background: rgba(133,153,234,0.15);"
            "border: 1px solid rgba(133,153,234,0.5); border-radius:8px; }"
            if selected
            else "DeviceCard { background: rgba(17,18,26,0.6);"
            "border: 1px solid #1e1f2b; border-radius:8px; }"
            "DeviceCard:hover { background: rgba(133,153,234,0.07);"
            "border-color: rgba(133,153,234,0.3); }"
        )

    def set_connected(self, connected: bool):
        self._connected = connected
        self._refresh_display()

    def update_profile(self, profile: SyncProfile):
        self.profile = profile
        self._refresh_display()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# SyncView — main view
# ---------------------------------------------------------------------------


class SyncView(QWidget):
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
        self.sync_manager = SyncManager(Session())
        self.profile_store = SyncProfileStore()
        self.mtp_manager = MtpManager()

        self.profiles: List[SyncProfile] = []
        self.current_profile: Optional[SyncProfile] = None
        self.cards: List[DeviceCard] = []
        self.selected_card: Optional[DeviceCard] = None
        self.sync_worker: Optional[SyncWorker] = None
        self.status_manager = StatusManager

        # Periodic MTP poll (every 5 s) to update connection badges
        self._mtp_poll_timer = QTimer(self)
        self._mtp_poll_timer.timeout.connect(self._poll_mtp_devices)
        if mtp_available():
            self._mtp_poll_timer.start(5000)

        self._init_ui()
        self._load_profiles()
        self._refresh_playlists()

    def showEvent(self, event):
        """Refresh playlists every time this view is shown — catches new playlists."""
        super().showEvent(event)
        self._refresh_playlists()

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
        section_lbl.setStyleSheet(
            "color:#555e7a; font-size:10px; letter-spacing:0.1em; font-weight:bold;"
        )
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
        self.placeholder.setStyleSheet(
            "color:#555e7a; font-style:italic; padding:40px;"
        )
        layout.addWidget(self.placeholder)

        # Tab widget (hidden until a profile is selected)
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        self.tabs.addTab(self._build_playlists_tab(), "Playlists")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.tabs.addTab(self._build_log_tab(), "Log")
        layout.addWidget(self.tabs, 1)

        return self.detail_panel

    def _build_playlists_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)

        # Toolbar: select all / none + track count label
        toolbar = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setFixedWidth(90)
        self.select_all_btn.clicked.connect(self._select_all_playlists)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setFixedWidth(90)
        self.select_none_btn.clicked.connect(self._select_no_playlists)

        self.track_count_label = QLabel("")
        self.track_count_label.setStyleSheet("color:#555e7a; font-size:11px;")

        toolbar.addWidget(self.select_all_btn)
        toolbar.addWidget(self.select_none_btn)
        toolbar.addStretch()
        toolbar.addWidget(self.track_count_label)
        layout.addLayout(toolbar)

        # Playlist checklist
        self.playlist_list = QListWidget()
        self.playlist_list.setAlternatingRowColors(False)
        self.playlist_list.itemChanged.connect(self._on_playlist_item_changed)
        layout.addWidget(self.playlist_list, 1)

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
        self.device_label.setStyleSheet("color:#555e7a;")
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
        self.folder_label.setStyleSheet("color:#555e7a;")
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
            "Clear destination before syncing  "
            "(removes existing music and playlist folders first)"
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
        self.sync_log.setStyleSheet(
            "background:#0b0c10; border:1px solid #1e1f2b; border-radius:6px;"
        )
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
        bar.setStyleSheet(
            "#SyncBottomBar { border-top: 1px solid #1e1f2b; "
            "background: rgba(11,12,16,0.95); }"
        )

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background:#1e1f2b; border-radius:3px; border:none; }"
            "QProgressBar::chunk { background:#8599ea; border-radius:3px; }"
        )
        layout.addWidget(self.progress_bar, 1)

        self.cancel_sync_btn = QPushButton("Cancel")
        self.cancel_sync_btn.setVisible(False)
        self.cancel_sync_btn.clicked.connect(self._cancel_sync)
        layout.addWidget(self.cancel_sync_btn)

        self.sync_btn = QPushButton("Start Sync  →")
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.setEnabled(False)
        self.sync_btn.setMinimumWidth(120)
        self.sync_btn.setStyleSheet(
            "QPushButton#PrimaryButton {"
            "  background: #8599ea; color: #0b0c10; font-weight:bold;"
            "  border-radius:6px; padding: 7px 18px;"
            "}"
            "QPushButton#PrimaryButton:hover { background:#9badf5; }"
            "QPushButton#PrimaryButton:disabled { background:#2a2c3e; color:#555e7a; }"
        )
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

        # Re-insert before the stretch
        stretch_item = self.card_layout.takeAt(self.card_layout.count() - 1)

        for profile in self.profiles:
            card = DeviceCard(profile, self._on_card_clicked, self.card_container)
            self.cards.append(card)
            self.card_layout.addWidget(card)

        self.card_layout.addStretch()

        # Restore connection badges
        self._poll_mtp_devices()

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

    def _find_card_for_profile(self, profile: SyncProfile) -> Optional[DeviceCard]:
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
        profile = SyncProfile(
            name=name.strip(),
            path="",
            music_path=MtpManager.DEFAULT_MUSIC_PATH,
        )
        self.profiles.append(profile)
        self.profile_store.save(self.profiles)
        self._rebuild_cards()
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

        self.profiles.remove(self.current_profile)
        self.current_profile = None
        self.profile_store.save(self.profiles)
        self._rebuild_cards()

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
        self.folder_label.setStyleSheet(
            "color:#b8c0f0;" if p.path else "color:#555e7a;"
        )

        self.clear_before_sync_check.blockSignals(True)
        self.clear_before_sync_check.setChecked(p.clear_before_sync)
        self.clear_before_sync_check.blockSignals(False)

        self._refresh_device_label()

        # Playlists tab
        self._apply_profile_playlist_selection()

    def _refresh_device_label(self):
        """Update the linked device label in the Settings tab."""
        if not self.current_profile:
            return
        if self.current_profile.device_uri:
            # Try to get a friendly name from live MTP devices
            devices = self.mtp_manager.list_devices() if mtp_available() else []
            match = next(
                (d for d in devices if d.uri == self.current_profile.device_uri),
                None,
            )
            if match:
                self.device_label.setText(match.display_name)
                self.device_label.setStyleSheet("color:#99EA85; font-weight:bold;")
            else:
                name = (
                    self.current_profile.device_name or self.current_profile.device_uri
                )
                self.device_label.setText(f"{name}  (not connected)")
                self.device_label.setStyleSheet("color:#555e7a;")
            self.unlink_device_btn.setVisible(True)
        else:
            self.device_label.setText("No device linked — using folder sync")
            self.device_label.setStyleSheet("color:#555e7a;")
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
        self.current_profile.clear_before_sync = (
            self.clear_before_sync_check.isChecked()
        )
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
            self.current_profile.path = folder
            self.folder_label.setText(folder)
            self.folder_label.setStyleSheet("color:#b8c0f0;")
            card = self._find_card_for_profile(self.current_profile)
            if card:
                card.update_profile(self.current_profile)
            self.profile_store.save(self.profiles)
            self._update_sync_button_state()

    def _link_device(self):
        """Show a picker of currently connected MTP devices."""
        devices = self.mtp_manager.list_devices()
        if not devices:
            QMessageBox.information(
                self,
                "No Devices Found",
                "No Android devices were detected.\n\n"
                "Make sure your phone is:\n"
                "  • Connected via USB\n"
                "  • Set to  File Transfer  mode\n"
                "    (pull down the notification shade and tap the USB notification)",
            )
            return

        options = [d.display_name for d in devices]
        choice, ok = QInputDialog.getItem(
            self, "Link Device", "Select device:", options, 0, False
        )
        if not ok:
            return

        chosen = devices[options.index(choice)]
        self.current_profile.device_uri = chosen.uri
        self.current_profile.device_name = chosen.short_name
        self.profile_store.save(self.profiles)

        self._refresh_device_label()
        card = self._find_card_for_profile(self.current_profile)
        if card:
            card.update_profile(self.current_profile)
            card._refresh_display()
        self._update_sync_button_state()

    def _unlink_device(self):
        if not self.current_profile:
            return
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
        self.detect_btn.setText("⟳ Detect")
        self.detect_btn.setEnabled(True)

        if not devices:
            QMessageBox.information(
                self,
                "No Devices Found",
                "No devices detected via USB.\n\n"
                "Make sure your phone is connected and set to File Transfer mode.\n"
                "(Pull down the notification shade and tap the USB notification.)",
            )
            return

        # Find devices not yet linked to any profile
        known_uris = {p.device_uri for p in self.profiles if p.device_uri}
        new_devices = [d for d in devices if d.uri not in known_uris]

        # Update connection badges regardless
        self._poll_mtp_devices()

        if not new_devices:
            QMessageBox.information(
                self,
                "Devices Up To Date",
                f"{len(devices)} device(s) connected — all already have profiles.",
            )
            return

        # Offer to create a profile for each new device
        for device in new_devices:
            reply = QMessageBox.question(
                self,
                "New Device Found",
                f"Found: {device.display_name}\n\n"
                "Create a sync profile for this device?",
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

    def _poll_mtp_devices(self):
        """Update connection badges on all cards silently."""
        if not mtp_available():
            return
        try:
            connected_uris = {d.uri for d in self.mtp_manager.list_devices()}
            for card in self.cards:
                if card.profile.device_uri:
                    card.set_connected(card.profile.device_uri in connected_uris)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Playlist helpers
    # -----------------------------------------------------------------------

    def _refresh_playlists(self):
        """Reload the full playlist list from the database."""
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        for playlist in self.sync_manager.get_playlists():
            item = QListWidgetItem()
            item.setText(f"{playlist['name']}  ({playlist['track_count']} tracks)")
            item.setToolTip(playlist.get("description") or "")
            item.setData(Qt.UserRole, playlist)
            item.setCheckState(Qt.Unchecked)
            self.playlist_list.addItem(item)
        self.playlist_list.blockSignals(False)

        if self.current_profile:
            self._apply_profile_playlist_selection()

    def _apply_profile_playlist_selection(self):
        """Tick the checkboxes that match the current profile's saved playlist IDs."""
        if not self.current_profile:
            return
        saved_ids = set(self.current_profile.playlist_ids)
        self.playlist_list.blockSignals(True)
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            pid = item.data(Qt.UserRole)["playlist_id"]
            item.setCheckState(Qt.Checked if pid in saved_ids else Qt.Unchecked)
        self.playlist_list.blockSignals(False)
        self._update_selected_playlists()

    def _save_current_profile_selections(self):
        """Write current checkbox state back into the active profile and persist."""
        if not self.current_profile:
            return
        ids = []
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole)["playlist_id"])
        self.current_profile.playlist_ids = ids
        self.current_profile.clear_before_sync = (
            self.clear_before_sync_check.isChecked()
        )
        self.profile_store.save(self.profiles)

    def _update_selected_playlists(self):
        """Rebuild self.selected_playlists and update the track count label."""
        self.selected_playlists = []
        total_tracks = 0
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            if item.checkState() == Qt.Checked:
                pl = item.data(Qt.UserRole)
                self.selected_playlists.append(pl)
                total_tracks += pl.get("track_count", 0)

        if self.selected_playlists:
            self.track_count_label.setText(
                f"{len(self.selected_playlists)} playlist(s)  ·  {total_tracks} tracks"
            )
        else:
            self.track_count_label.setText("")

        self._update_sync_button_state()

    def _on_playlist_item_changed(self, item: QListWidgetItem):
        self._update_selected_playlists()
        self._save_current_profile_selections()

    def _select_all_playlists(self):
        self.playlist_list.blockSignals(True)
        for i in range(self.playlist_list.count()):
            self.playlist_list.item(i).setCheckState(Qt.Checked)
        self.playlist_list.blockSignals(False)
        self._update_selected_playlists()
        self._save_current_profile_selections()

    def _select_no_playlists(self):
        self.playlist_list.blockSignals(True)
        for i in range(self.playlist_list.count()):
            self.playlist_list.item(i).setCheckState(Qt.Unchecked)
        self.playlist_list.blockSignals(False)
        self._update_selected_playlists()
        self._save_current_profile_selections()

    # -----------------------------------------------------------------------
    # Sync button state
    # -----------------------------------------------------------------------

    def _update_sync_button_state(self):
        """Enable the sync button only when a valid destination and playlists exist."""
        if not self.current_profile or not self.selected_playlists:
            self.sync_btn.setEnabled(False)
            return

        has_destination = bool(self.current_profile.device_uri) or bool(
            self.current_profile.path
        )
        self.sync_btn.setEnabled(has_destination)

    # -----------------------------------------------------------------------
    # Sync execution
    # -----------------------------------------------------------------------

    def _start_sync(self):
        if not self.current_profile or not self.selected_playlists:
            return

        # Validate destination
        if self.current_profile.is_mtp:
            if not self.current_profile.device_uri:
                QMessageBox.warning(self, "No Device", "No device linked.")
                return
            name = self.current_profile.device_name or self.current_profile.device_uri
            dest_desc = (
                f"Device: {name}\nMusic folder: {self.current_profile.music_path}"
            )
        else:
            if not self.current_profile.path:
                QMessageBox.warning(self, "No Folder", "No destination folder set.")
                return
            dest_desc = f"Folder: {self.current_profile.path}"

        total_tracks = sum(p["track_count"] for p in self.selected_playlists)
        clear = self.current_profile.clear_before_sync

        confirm_msg = (
            f"Sync {len(self.selected_playlists)} playlist(s) "
            f"({total_tracks} tracks) to:\n\n"
            f"{dest_desc}"
        )
        if clear:
            confirm_msg += "\n\n⚠️  Destination will be cleared first."

        reply = QMessageBox.question(
            self, "Confirm Sync", confirm_msg, QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Switch to Log tab so the user can see progress
        self.tabs.setCurrentIndex(2)

        self.status_manager.start_task(f"Starting sync: {self.current_profile.name}")
        self._set_sync_ui_state(False)
        self.progress_bar.setVisible(True)
        self.sync_log.clear()
        self.sync_log.append(f"Starting sync → {dest_desc}")
        if clear:
            self.sync_log.append("⚠️  Clearing destination first…")

        self.sync_worker = SyncWorker(
            self.sync_manager,
            self.selected_playlists,
            self.current_profile,
        )
        self.sync_worker.progress.connect(self._on_sync_progress)
        self.sync_worker.playlist_complete.connect(self._on_playlist_complete)
        self.sync_worker.finished.connect(self._on_sync_finished)
        self.sync_worker.start()

    def _cancel_sync(self):
        if self.sync_worker and self.sync_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Sync",
                "Cancel the running sync?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.sync_worker.cancel()
                self.sync_log.append("*** Sync cancelled by user ***")
                self.status_manager.end_task("Sync cancelled", 3000)

    def _set_sync_ui_state(self, idle: bool):
        self.sync_btn.setVisible(idle)
        self.cancel_sync_btn.setVisible(not idle)
        self.add_profile_btn.setEnabled(idle)

    # -----------------------------------------------------------------------
    # Sync signal handlers
    # -----------------------------------------------------------------------

    def _on_sync_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        self.current_action.setText(message)
        pct = f"  ({current / total * 100:.0f}%)" if total > 0 else ""
        self.status_manager.show_message(f"Syncing: {message}{pct}", 0)

    def _on_playlist_complete(self, result: Dict):
        icon = "✅" if result["success"] else "❌"
        skipped = result.get("tracks_skipped", 0)
        skip_note = f"  ({skipped} duplicates skipped)" if skipped else ""
        self.sync_log.append(
            f"{icon} {result['playlist_name']}: {result['message']}{skip_note}"
        )
        self.sync_log.verticalScrollBar().setValue(
            self.sync_log.verticalScrollBar().maximum()
        )

    def _on_sync_finished(self, results: List[Dict]):
        self._set_sync_ui_state(True)
        self.progress_bar.setVisible(False)

        successful = sum(1 for r in results if r["success"])
        total = len(results)
        total_copied = sum(r.get("tracks_copied", 0) for r in results)
        total_skipped = sum(r.get("tracks_skipped", 0) for r in results)

        self.current_action.setText(
            f"Done — {successful}/{total} playlists  ·  "
            f"{total_copied} copied, {total_skipped} skipped"
        )
        self.sync_log.append(
            f"\n=== Sync complete: {successful}/{total} playlists  |  "
            f"{total_copied} copied, {total_skipped} skipped ==="
        )

        if successful > 0:
            self.status_manager.end_task(
                f"Sync complete: {total_copied} copied, {total_skipped} skipped", 5000
            )
            QMessageBox.information(
                self,
                "Sync Complete",
                f"Sync finished!\n\n"
                f"Playlists:  {successful}/{total} successful\n"
                f"Tracks copied:  {total_copied}\n"
                f"Duplicates skipped:  {total_skipped}",
            )
        else:
            self.status_manager.end_task("Sync completed — no tracks copied", 3000)
            QMessageBox.warning(
                self,
                "Sync Complete",
                "No tracks were copied.\n\n"
                "Check that source files exist and the destination is writable.",
            )
