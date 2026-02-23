from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db_helpers import Session
from logger_config import logger
from status_utility import StatusManager
from sync_utility import SyncManager, SyncProfile, SyncProfileStore, SyncWorker


class SyncView(QWidget):
    """Sync view with named device profiles, duplicate detection, and clear-before-sync."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.sync_manager = SyncManager(Session())
        self.profile_store = SyncProfileStore()
        self.profiles: List[SyncProfile] = []
        self.current_profile: Optional[SyncProfile] = None
        self.selected_playlists: List[Dict] = []
        self.sync_worker = None
        self.status_manager = StatusManager

        self._init_ui()
        self._load_profiles()
        self._refresh_playlists()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Music Sync")
        title.setObjectName("ViewTitle")
        layout.addWidget(title)

        desc = QLabel(
            "Create sync profiles for each of your devices. "
            "Each profile remembers the destination folder, selected playlists, and sync options."
        )
        desc.setWordWrap(True)
        desc.setObjectName("ViewDescription")
        layout.addWidget(desc)

        # ---- Profile management ----
        profile_group = QGroupBox("1. Sync Profile")
        profile_layout = QVBoxLayout(profile_group)

        # Profile list + buttons side by side
        profile_row = QHBoxLayout()

        self.profile_list = QListWidget()
        self.profile_list.setMaximumHeight(130)
        self.profile_list.currentRowChanged.connect(self._on_profile_selected)
        profile_row.addWidget(self.profile_list)

        profile_btn_col = QVBoxLayout()
        self.new_profile_btn = QPushButton("New Profile")
        self.new_profile_btn.clicked.connect(self._new_profile)
        profile_btn_col.addWidget(self.new_profile_btn)

        self.rename_profile_btn = QPushButton("Rename")
        self.rename_profile_btn.clicked.connect(self._rename_profile)
        self.rename_profile_btn.setEnabled(False)
        profile_btn_col.addWidget(self.rename_profile_btn)

        self.delete_profile_btn = QPushButton("Delete")
        self.delete_profile_btn.clicked.connect(self._delete_profile)
        self.delete_profile_btn.setEnabled(False)
        profile_btn_col.addWidget(self.delete_profile_btn)

        profile_btn_col.addStretch()
        profile_row.addLayout(profile_btn_col)
        profile_layout.addLayout(profile_row)

        # Destination folder for current profile
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Destination:"))
        self.device_info = QLabel("No profile selected")
        self.device_info.setWordWrap(True)
        folder_row.addWidget(self.device_info, 1)
        self.browse_btn = QPushButton("Change Folder…")
        self.browse_btn.clicked.connect(self._browse_device)
        self.browse_btn.setEnabled(False)
        folder_row.addWidget(self.browse_btn)
        profile_layout.addLayout(folder_row)

        layout.addWidget(profile_group)

        # ---- Playlist selection ----
        playlist_group = QGroupBox("2. Select Playlists")
        playlist_layout = QVBoxLayout(playlist_group)

        quick_select_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self._select_all_playlists)
        quick_select_layout.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.clicked.connect(self._select_none_playlists)
        quick_select_layout.addWidget(self.select_none_btn)

        quick_select_layout.addStretch()

        self.selected_count_label = QLabel("0 playlists selected")
        quick_select_layout.addWidget(self.selected_count_label)
        playlist_layout.addLayout(quick_select_layout)

        self.playlist_list = QListWidget()
        self.playlist_list.itemChanged.connect(self._on_playlist_selection_changed)
        playlist_layout.addWidget(self.playlist_list)

        layout.addWidget(playlist_group)

        # ---- Sync options ----
        options_group = QGroupBox("3. Sync Options")
        options_layout = QVBoxLayout(options_group)

        self.clear_before_sync_check = QCheckBox(
            "Clear destination before syncing  "
            "(removes existing music/ and playlists/ folders first)"
        )
        self.clear_before_sync_check.toggled.connect(self._on_option_changed)
        options_layout.addWidget(self.clear_before_sync_check)

        layout.addWidget(options_group)

        # ---- Progress ----
        progress_group = QGroupBox("4. Sync Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.current_action = QLabel("Ready to sync")
        progress_layout.addWidget(self.current_action)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)

        self.sync_log = QTextEdit()
        self.sync_log.setMaximumHeight(120)
        self.sync_log.setFont(QFont("Courier", 8))
        progress_layout.addWidget(self.sync_log)

        layout.addWidget(progress_group)

        # ---- Control buttons ----
        button_layout = QHBoxLayout()

        self.cancel_sync_btn = QPushButton("Cancel Sync")
        self.cancel_sync_btn.clicked.connect(self._cancel_sync)
        self.cancel_sync_btn.setVisible(False)
        button_layout.addWidget(self.cancel_sync_btn)

        button_layout.addStretch()

        self.sync_btn = QPushButton("Start Sync")
        self.sync_btn.clicked.connect(self._start_sync)
        self.sync_btn.setEnabled(False)
        button_layout.addWidget(self.sync_btn)

        layout.addLayout(button_layout)
        layout.addStretch()

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def _load_profiles(self):
        self.profiles = self.profile_store.load()
        self._rebuild_profile_list()
        if self.profiles:
            self.profile_list.setCurrentRow(0)

    def _rebuild_profile_list(self):
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self.profiles:
            item = QListWidgetItem(profile.name)
            item.setData(Qt.UserRole, profile)
            self.profile_list.addItem(item)
        self.profile_list.blockSignals(False)

    def _on_profile_selected(self, row: int):
        if row < 0 or row >= len(self.profiles):
            self.current_profile = None
            self._set_profile_controls_enabled(False)
            self.device_info.setText("No profile selected")
            return

        # Save playlist selection of the previous profile first
        if self.current_profile is not None:
            self._save_current_profile_selections()

        self.current_profile = self.profiles[row]
        self._set_profile_controls_enabled(True)

        # Update folder label
        path = self.current_profile.path
        self.device_info.setText(
            path if path else "No folder set — click Change Folder…"
        )

        # Load this profile's options
        self.clear_before_sync_check.blockSignals(True)
        self.clear_before_sync_check.setChecked(self.current_profile.clear_before_sync)
        self.clear_before_sync_check.blockSignals(False)

        # Update playlist checkboxes to match profile
        self._apply_profile_playlist_selection()
        self._update_sync_button_state()

    def _set_profile_controls_enabled(self, enabled: bool):
        self.rename_profile_btn.setEnabled(enabled)
        self.delete_profile_btn.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New Sync Profile", "Profile name:")
        if not ok or not name.strip():
            return
        profile = SyncProfile(name=name.strip(), path="")
        self.profiles.append(profile)
        self._rebuild_profile_list()
        self.profile_list.setCurrentRow(len(self.profiles) - 1)
        self.profile_store.save(self.profiles)

    def _rename_profile(self):
        if not self.current_profile:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Profile", "New name:", text=self.current_profile.name
        )
        if not ok or not name.strip():
            return
        self.current_profile.name = name.strip()
        self._rebuild_profile_list()
        # Reselect the same profile
        idx = self.profiles.index(self.current_profile)
        self.profile_list.blockSignals(True)
        self.profile_list.setCurrentRow(idx)
        self.profile_list.blockSignals(False)
        self.profile_store.save(self.profiles)

    def _delete_profile(self):
        if not self.current_profile:
            return
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{self.current_profile.name}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.profiles.remove(self.current_profile)
        self.current_profile = None
        self._rebuild_profile_list()
        self.profile_store.save(self.profiles)
        if self.profiles:
            self.profile_list.setCurrentRow(0)
        else:
            self.device_info.setText("No profile selected")
            self._update_sync_button_state()

    def _browse_device(self):
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
            self.device_info.setText(folder)
            self.profile_store.save(self.profiles)
            self._update_sync_button_state()

    def _save_current_profile_selections(self):
        """Persist the current playlist checkbox state into the active profile."""
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

    def _apply_profile_playlist_selection(self):
        """Update playlist checkboxes to match the current profile's stored IDs."""
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

    def _on_option_changed(self):
        if self.current_profile:
            self.current_profile.clear_before_sync = (
                self.clear_before_sync_check.isChecked()
            )
            self.profile_store.save(self.profiles)

    # ------------------------------------------------------------------
    # Playlist helpers
    # ------------------------------------------------------------------

    def _refresh_playlists(self):
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        playlists = self.sync_manager.get_playlists()
        for playlist in playlists:
            item = QListWidgetItem()
            item.setText(f"{playlist['name']} ({playlist['track_count']} tracks)")
            if playlist.get("description"):
                item.setToolTip(playlist["description"])
            item.setData(Qt.UserRole, playlist)
            item.setCheckState(Qt.Unchecked)
            self.playlist_list.addItem(item)
        self.playlist_list.blockSignals(False)

        # Re-apply current profile selection if one is active
        if self.current_profile:
            self._apply_profile_playlist_selection()

    def _select_all_playlists(self):
        self.playlist_list.blockSignals(True)
        for i in range(self.playlist_list.count()):
            self.playlist_list.item(i).setCheckState(Qt.Checked)
        self.playlist_list.blockSignals(False)
        self._update_selected_playlists()
        self._save_current_profile_selections()
        self._update_sync_button_state()

    def _select_none_playlists(self):
        self.playlist_list.blockSignals(True)
        for i in range(self.playlist_list.count()):
            self.playlist_list.item(i).setCheckState(Qt.Unchecked)
        self.playlist_list.blockSignals(False)
        self._update_selected_playlists()
        self._save_current_profile_selections()
        self._update_sync_button_state()

    def _on_playlist_selection_changed(self, item):
        self._update_selected_playlists()
        self._save_current_profile_selections()
        self._update_sync_button_state()

    def _update_selected_playlists(self):
        self.selected_playlists = []
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            if item.checkState() == Qt.Checked:
                self.selected_playlists.append(item.data(Qt.UserRole))
        total_tracks = sum(p["track_count"] for p in self.selected_playlists)
        self.selected_count_label.setText(
            f"{len(self.selected_playlists)} playlists selected ({total_tracks} total tracks)"
        )

    # ------------------------------------------------------------------
    # Sync state helpers
    # ------------------------------------------------------------------

    def _update_sync_button_state(self):
        has_profile = self.current_profile is not None
        has_path = bool(self.current_profile and self.current_profile.path)
        has_playlists = len(self.selected_playlists) > 0
        self.sync_btn.setEnabled(has_profile and has_path and has_playlists)

    def _set_sync_ui_state(self, enabled: bool):
        self.profile_list.setEnabled(enabled)
        self.new_profile_btn.setEnabled(enabled)
        self.rename_profile_btn.setEnabled(enabled and self.current_profile is not None)
        self.delete_profile_btn.setEnabled(enabled and self.current_profile is not None)
        self.browse_btn.setEnabled(enabled and self.current_profile is not None)
        self.playlist_list.setEnabled(enabled)
        self.select_all_btn.setEnabled(enabled)
        self.select_none_btn.setEnabled(enabled)
        self.clear_before_sync_check.setEnabled(enabled)
        self.sync_btn.setEnabled(enabled and len(self.selected_playlists) > 0)
        self.cancel_sync_btn.setVisible(not enabled)

    # ------------------------------------------------------------------
    # Sync execution
    # ------------------------------------------------------------------

    def _start_sync(self):
        if not self.current_profile or not self.current_profile.path:
            QMessageBox.warning(
                self, "Error", "Please select a destination folder for this profile."
            )
            return

        device_path = self.current_profile.path
        total_tracks = sum(p["track_count"] for p in self.selected_playlists)
        clear = self.clear_before_sync_check.isChecked()

        confirm_msg = (
            f"Sync {len(self.selected_playlists)} playlists ({total_tracks} tracks) to:\n"
            f"{device_path}"
        )
        if clear:
            confirm_msg += (
                "\n\n⚠️  The music/ and playlists/ folders will be cleared first."
            )

        reply = QMessageBox.question(
            self, "Confirm Sync", confirm_msg, QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.status_manager.start_task(f"Starting sync to {device_path}")
        self._set_sync_ui_state(False)
        self.progress_bar.setVisible(True)
        self.sync_log.clear()
        self.sync_log.append(
            f"Starting sync to: {device_path}" + (" (clearing first)" if clear else "")
        )

        self.sync_worker = SyncWorker(
            self.sync_manager,
            self.selected_playlists,
            device_path,
            clear_before_sync=clear,
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
                "Cancel the sync operation?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.sync_worker.cancel()
                self.sync_log.append("*** Sync cancelled by user ***")
                self.status_manager.end_task("Sync cancelled", 3000)

    # ------------------------------------------------------------------
    # Sync signal handlers
    # ------------------------------------------------------------------

    def _on_sync_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        self.current_action.setText(message)
        if total > 0:
            pct = (current / total) * 100
            self.status_manager.show_message(f"Syncing: {message} ({pct:.1f}%)", 0)
        else:
            self.status_manager.show_message(f"Syncing: {message}", 0)

    def _on_playlist_complete(self, result: Dict):
        status = "✅" if result["success"] else "❌"
        skipped = result.get("tracks_skipped", 0)
        skip_note = f"  ({skipped} duplicates skipped)" if skipped else ""
        self.sync_log.append(
            f"{status} {result['playlist_name']}: {result['message']}{skip_note}"
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
            f"Sync complete: {successful}/{total} playlists successful"
        )
        self.sync_log.append(
            f"\n=== Sync complete: {successful}/{total} playlists | "
            f"{total_copied} copied, {total_skipped} duplicates skipped ==="
        )

        if successful > 0:
            self.status_manager.end_task(
                f"Sync complete: {total_copied} copied, {total_skipped} skipped", 5000
            )
            QMessageBox.information(
                self,
                "Sync Complete",
                f"Sync completed!\n\n"
                f"Playlists: {successful}/{total} successful\n"
                f"Tracks copied: {total_copied}\n"
                f"Duplicates skipped: {total_skipped}\n\n"
                f"Files are in: music/\n"
                f"Playlists are in: playlists/",
            )
        else:
            self.status_manager.end_task("Sync completed with no tracks copied", 3000)
            QMessageBox.warning(
                self,
                "Sync Complete",
                "No tracks were copied. Check source files exist and destination is writable.",
            )

    # ------------------------------------------------------------------
    # Refresh (called when switching to this view)
    # ------------------------------------------------------------------

    def refresh(self):
        logger.debug("Sync view refreshed")
        self._refresh_playlists()
