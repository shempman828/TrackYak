"""SyncView: the device sync screen (profile sidebar, Music / Options / Activity pages, bottom bar)."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
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
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.common.widgets.segmented_control import SegmentedControl
from src.common.widgets.style_utils import set_style_property
from src.db.db_helpers import Session
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager
from src.sync.device_card import DEVICE_GLYPH, FOLDER_GLYPH, DeviceCard
from src.sync.mtp_list_worker import MtpListWorker
from src.sync.mtp_manager import MtpDevice, MtpManager, mtp_available
from src.sync.sync_activity_panel import SyncActivityPanel
from src.sync.sync_device_mixin import SyncDeviceMixin
from src.sync.sync_execution_mixin import SyncExecutionMixin
from src.sync.sync_manager import SyncManager
from src.sync.sync_options_mixin import SyncOptionsMixin
from src.sync.sync_profile import SyncProfile, SyncProfileStore
from src.sync.sync_selection_mixin import SyncSelectionMixin
from src.sync.sync_selection_tree import SyncSelectionTree
from src.sync.sync_worker import SyncWorker

# Pages of the detail area (self.tabs) and segments of self.tab_switch.
MUSIC_PAGE, OPTIONS_PAGE, ACTIVITY_PAGE = 0, 1, 2
PAGE_TITLES = ["Music", "Options", "Activity"]

# ---------------------------------------------------------------------------
# SyncView — main view
# ---------------------------------------------------------------------------


class SyncView(SyncSelectionMixin, SyncOptionsMixin, SyncDeviceMixin, SyncExecutionMixin, QWidget):
    """Device sync view: profile cards, a header, Music/Options/Activity pages, and a live bottom bar."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        # The scoped_session proxy, not a resolved Session: SyncManager runs on this thread and on
        # worker threads, and the proxy gives each calling thread its own Session.
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

        # Each scan (poll, Link, Detect) gets its own worker so a user action is never dropped
        # because a poll is in flight; _known_mtp_devices caches the latest result for the header.
        self._known_mtp_devices: list[MtpDevice] = []
        self._mtp_list_worker: MtpListWorker | None = None
        self._link_worker: MtpListWorker | None = None
        self._detect_worker: MtpListWorker | None = None

        # Started in showEvent / stopped in hideEvent: no gio polling while the view is hidden.
        self._mtp_poll_timer = QTimer(self)
        self._mtp_poll_timer.timeout.connect(self._refresh_mtp_devices)

        self._init_ui()
        self._load_profiles()
        self._refresh_sync_items()

    def showEvent(self, event):
        """Refresh playlists/moods (catches new ones) and start polling for devices."""
        super().showEvent(event)
        self._refresh_sync_items()
        self._start_mtp_polling()

    def hideEvent(self, event):
        """Stop polling for devices while the view is hidden."""
        super().hideEvent(event)
        self._stop_mtp_polling()

    def resizeEvent(self, event):
        """Re-elide the header's destination path to the new width."""
        super().resizeEvent(event)
        if self.current_profile:
            self._refresh_header()

    def closeEvent(self, event):
        """Stop background work before teardown."""
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self):
        """Stop polling, save pending settings, then cancel and join every worker thread."""
        # MainWindow.closeEvent calls this: a QThread destroyed while running aborts the process.
        timer = getattr(self, "_mtp_poll_timer", None)
        if timer is not None:
            timer.stop()
        cache_timer = getattr(self, "_cache_save_timer", None)
        if cache_timer is not None and cache_timer.isActive():
            self._flush_cache_max()
        for name in ("sync_worker", "_sync_items_loader", "_mtp_list_worker", "_link_worker", "_detect_worker"):
            worker = getattr(self, name, None)
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                worker.wait()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _init_ui(self):
        """Lay out sidebar | detail panel over the bottom bar."""
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

    def _build_sidebar(self) -> QWidget:
        """The DEVICES list of profile cards with New / Detect below."""
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
        self.detect_btn.setToolTip("Scan for connected Android devices via USB" if mtp_available() else "Install gvfs-backends (sudo apt install gvfs-backends) to enable device detection")
        self.detect_btn.setEnabled(mtp_available())
        self.detect_btn.clicked.connect(self._detect_devices)
        btn_row.addWidget(self.detect_btn, 1)

        layout.addLayout(btn_row)
        return sidebar

    def _build_detail_panel(self) -> QWidget:
        """The empty state, or the profile page (header, page switch, pages)."""
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
        """The first-run card: detect a phone, choose a folder, or make an empty profile."""
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

        body = QLabel("Make one profile for each device. Pick the playlists and moods to copy, and each sync keeps the device up to date.")
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
            "Connect the phone with USB and set it to File Transfer mode." if mtp_available() else "Install gvfs-backends (sudo apt install gvfs-backends) to enable device detection"
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
        """Profile icon, name, connection pill, destination, Change… and the ⋯ menu."""
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
        self.profile_menu_btn.setAccessibleName("Profile actions")
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
        """The Music page: filter + bulk actions over the playlist/mood checklist."""
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

        self.select_all_btn = link("Select All", "Tick every playlist and mood the filter shows", self._select_all_items)
        self.select_none_btn = link("Select None", "Untick every playlist and mood the filter shows", self._select_no_items)
        separator = QLabel("·")
        separator.setProperty("textRole", "muted")
        toolbar.addWidget(separator)
        self.expand_all_btn = link("Expand All", "Show all sub-playlists", self._expand_all_items)
        self.collapse_all_btn = link("Collapse All", "Hide all sub-playlists", self._collapse_all_items)
        layout.addLayout(toolbar)

        self.sync_tree = SyncSelectionTree()
        self.sync_tree.itemChanged.connect(self._on_sync_item_changed)
        self.sync_tree.bulkCheckChanged.connect(self._on_sync_tree_bulk_changed)
        self.sync_tree.set_placeholder_text("Loading playlists and moods…")
        layout.addWidget(self.sync_tree, 1)

        hint = QLabel("Right-click a playlist to select or clear it with all its sub-playlists.")
        hint.setProperty("textRole", "muted")
        hint.setObjectName("SyncHint")
        layout.addWidget(hint)

        return w

    def _build_log_tab(self) -> QWidget:
        """The Activity page."""
        self.activity = SyncActivityPanel()
        self.sync_log = self.activity.sync_log
        return self.activity

    def _build_bottom_bar(self) -> QWidget:
        """Idle: selection summary + Sync now (and the last result). Running: step, progress, Cancel."""
        bar = QWidget()
        bar.setObjectName("SyncBottomBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 16, 10)
        layout.setSpacing(12)

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
        self.progress_bar.setAccessibleName("Sync progress")
        layout.addWidget(self.progress_bar)

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
        """Show a page; opening Activity clears its attention dot."""
        self.tabs.setCurrentIndex(index)
        if index == ACTIVITY_PAGE:
            self._set_activity_attention(False)

    def _set_activity_attention(self, on: bool):
        """Dot the Activity segment while it has news the user hasn't looked at."""
        on = on and self.tabs.currentIndex() != ACTIVITY_PAGE
        self.tab_switch.setItemText(ACTIVITY_PAGE, "Activity  •" if on else "Activity")
        set_style_property(self.tab_switch.button(ACTIVITY_PAGE), "attention", on)

    def _set_sync_ui_state(self, idle: bool):
        """Swap the bottom bar between idle and running, and lock profile-level actions during a run."""
        self.sync_btn.setVisible(idle)
        self.track_count_label.setVisible(idle)
        self.result_widget.setVisible(idle)
        self.cancel_sync_btn.setVisible(not idle)
        self.current_action.setVisible(not idle)
        self.progress_bar.setVisible(not idle)
        self.add_profile_btn.setEnabled(idle)
        self.detect_btn.setEnabled(idle and mtp_available() and not self._scan_in_progress(self._detect_worker))
        # Deleting or renaming the profile mid-run would orphan the worker's view of it.
        self.profile_menu_btn.setEnabled(idle)
        if not idle:
            # The running sync may be reading cached MP3s; _on_sync_finished re-enables it.
            self.clear_cache_btn.setEnabled(False)
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
        """Remove the last run's result from the bottom bar."""
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
        self._update_connection_badges({d.uri for d in self._known_mtp_devices})
        self._refresh_mtp_devices()

    def _on_card_clicked(self, card: DeviceCard):
        """Select a card and load its profile."""
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
        """The sidebar card showing `profile`, if any."""
        return next((card for card in self.cards if card.profile is profile), None)

    def _refresh_current_card(self):
        """Redraw the current profile's card."""
        card = self._find_card_for_profile(self.current_profile) if self.current_profile else None
        if card:
            card.update_profile(self.current_profile)

    def _update_selected_items(self):
        """Recompute the selection, then show its track total on the current card."""
        super()._update_selected_items()
        card = self._find_card_for_profile(self.current_profile) if self.current_profile else None
        if card:
            card.set_track_total(self._selection_totals[0])

    def _save_current_profile_selections(self):
        """Persist the ticked items, then redraw the current card."""
        super()._save_current_profile_selections()
        self._refresh_current_card()

    # -----------------------------------------------------------------------
    # Profile CRUD
    # -----------------------------------------------------------------------

    def _load_profiles(self):
        """Load profiles from disk and select the first one."""
        self.profiles = self.profile_store.load()
        self._rebuild_cards()
        if self.profiles:
            self._on_card_clicked(self.cards[0])
        else:
            self._show_placeholder()

    def _add_and_select_profile(self, profile: SyncProfile):
        """Save a new profile and select its card."""
        self.profiles.append(profile)
        self.profile_store.save(self.profiles)
        self._rebuild_cards()
        logger.info(f"Created new sync profile: {profile.name}")
        new_card = self._find_card_for_profile(profile)
        if new_card:
            self._on_card_clicked(new_card)

    def _new_profile(self):
        """Ask for a name and create an empty profile."""
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        self._add_and_select_profile(SyncProfile(name=name.strip(), path="", music_path=MtpManager.DEFAULT_MUSIC_PATH))
        self.tab_switch.setCurrentIndex(OPTIONS_PAGE)  # a blank profile needs a destination next

    def _new_folder_profile(self):
        """Empty-state shortcut: pick a folder and make a profile for it."""
        folder = QFileDialog.getExistingDirectory(self, "Select Sync Destination Folder", "", QFileDialog.ShowDirsOnly)
        if not folder:
            return
        name = Path(folder).name or folder
        self._add_and_select_profile(SyncProfile(name=name, path=folder, music_path=MtpManager.DEFAULT_MUSIC_PATH))

    def _rename_profile(self):
        """Rename the current profile."""
        if not self.current_profile:
            return
        name, ok = QInputDialog.getText(self, "Rename Profile", "Profile name:", text=self.current_profile.name)
        new_name = name.strip()
        if not ok or not new_name or new_name == self.current_profile.name:
            return
        self.current_profile.name = new_name
        self.profile_store.save(self.profiles)
        self._refresh_current_card()
        self._refresh_header()

    def _delete_profile(self):
        """Delete the current profile (no files are touched) after confirmation."""
        if not self.current_profile:
            return
        reply = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{self.current_profile.name}'?\n\nThis only removes the profile — no files are deleted.", QMessageBox.Yes | QMessageBox.No
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
        """Show the empty state."""
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
        self._load_options_into_ui()
        self._refresh_header()
        self._apply_profile_selection()

    def _refresh_header(self):
        """Update the profile header and the Options page's linked-device row."""
        p = self.current_profile
        if not p:
            return
        self.profile_title.setText(p.name)
        self.profile_icon.setText(DEVICE_GLYPH if p.is_mtp else FOLDER_GLYPH)

        if p.device_uri:
            # From the last background scan: never enumerate devices on the GUI thread.
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

    def _change_destination(self):
        """Header button: re-link the device or pick another folder."""
        if not self.current_profile:
            return
        if self.current_profile.is_mtp:
            self._link_device()
        else:
            self._browse_folder()
