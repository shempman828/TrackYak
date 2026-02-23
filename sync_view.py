import os
from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
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
from sync_utility import SyncDevice, SyncManager, SyncWorker


class SyncView(QWidget):
    """Sync view that provides access to the sync functionality."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.sync_manager = SyncManager(Session())
        self.selected_playlists = []
        self.sync_worker = None
        self.status_manager = StatusManager

        self._init_ui()
        self._refresh_devices()
        self._refresh_playlists()

    def _init_ui(self):
        """Initialize the sync view UI."""
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)

        # Title
        title = QLabel("Music Sync")
        title.setObjectName("ViewTitle")
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Sync your music and playlists to external devices or folders. "
            "This will copy your music files and create M3U playlists for compatible devices."
        )
        desc.setWordWrap(True)
        desc.setObjectName("ViewDescription")
        layout.addWidget(desc)

        # Device selection
        device_group = QGroupBox("1. Select Destination")
        device_layout = QVBoxLayout(device_group)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Folder:"))
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_row.addWidget(self.device_combo)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_device)
        device_row.addWidget(self.browse_btn)

        device_layout.addLayout(device_row)

        self.device_info = QLabel("Select a destination folder")
        device_layout.addWidget(self.device_info)

        layout.addWidget(device_group)

        # Playlist selection
        playlist_group = QGroupBox("2. Select Playlists")
        playlist_layout = QVBoxLayout(playlist_group)

        # Quick selection buttons
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

        # Playlist list
        self.playlist_list = QListWidget()
        self.playlist_list.itemChanged.connect(self._on_playlist_selection_changed)
        playlist_layout.addWidget(self.playlist_list)

        layout.addWidget(playlist_group)

        # Progress area
        progress_group = QGroupBox("3. Sync Progress")
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

        # Control buttons
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

    def _refresh_devices(self):
        """Refresh list of available devices."""
        self.device_combo.clear()
        devices = self.sync_manager.discover_devices()

        for device in devices:
            self.device_combo.addItem(device.name, device)

    def _refresh_playlists(self):
        """Refresh list of playlists from database."""
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

    def _on_device_changed(self, index):
        """Handle device selection change."""
        if index >= 0:
            device = self.device_combo.itemData(index)
            if device.path:
                self.device_info.setText(f"Destination: {device.path}")
            else:
                self.device_info.setText("Click Browse to select folder")
            self._update_sync_button_state()

    def _browse_device(self):
        """Browse for custom device folder."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Sync Destination Folder", "", QFileDialog.ShowDirsOnly
        )

        if folder:
            custom_device = SyncDevice(
                f"Selected: {os.path.basename(folder)}", folder, "custom"
            )
            self.device_combo.addItem(custom_device.name, custom_device)
            self.device_combo.setCurrentIndex(self.device_combo.count() - 1)

    def _select_all_playlists(self):
        """Select all playlists."""
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            item.setCheckState(Qt.Checked)

    def _select_none_playlists(self):
        """Deselect all playlists."""
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            item.setCheckState(Qt.Unchecked)

    def _on_playlist_selection_changed(self, item):
        """Handle playlist selection changes."""
        self._update_selected_playlists()
        self._update_sync_button_state()

    def _update_selected_playlists(self):
        """Update list of selected playlists."""
        self.selected_playlists = []
        for i in range(self.playlist_list.count()):
            item = self.playlist_list.item(i)
            if item.checkState() == Qt.Checked:
                playlist_data = item.data(Qt.UserRole)
                self.selected_playlists.append(playlist_data)

        # Update count label
        total_tracks = sum(p["track_count"] for p in self.selected_playlists)
        self.selected_count_label.setText(
            f"{len(self.selected_playlists)} playlists selected ({total_tracks} total tracks)"
        )

    def _update_sync_button_state(self):
        """Enable sync button only when device and playlists are selected."""
        has_device = self.device_combo.currentIndex() >= 0
        has_playlists = len(self.selected_playlists) > 0

        device = self.device_combo.currentData()
        has_path = device and device.path if device else False

        self.sync_btn.setEnabled(has_device and has_playlists and has_path)

    def _start_sync(self):
        """Start the sync operation."""
        device = self.device_combo.currentData()
        if not device or not device.path:
            QMessageBox.warning(self, "Error", "Please select a destination folder")
            return

        # Confirm sync
        total_tracks = sum(p["track_count"] for p in self.selected_playlists)
        reply = QMessageBox.question(
            self,
            "Confirm Sync",
            f"Sync {len(self.selected_playlists)} playlists ({total_tracks} tracks) to:\n{device.path}?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        # Show status for sync start
        self.status_manager.start_task(f"Starting sync to {device.path}")

        # Disable UI during sync
        self._set_sync_ui_state(False)
        self.progress_bar.setVisible(True)
        self.sync_log.clear()
        self.sync_log.append("Starting sync...")

        # Start sync worker
        self.sync_worker = SyncWorker(
            self.sync_manager, self.selected_playlists, device.path
        )

        self.sync_worker.progress.connect(self._on_sync_progress)
        self.sync_worker.playlist_complete.connect(self._on_playlist_complete)
        self.sync_worker.finished.connect(self._on_sync_finished)

        self.sync_worker.start()

    def _cancel_sync(self):
        """Cancel ongoing sync operation."""
        if self.sync_worker and self.sync_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Sync",
                "Are you sure you want to cancel the sync operation?",
                QMessageBox.Yes | QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                self.sync_worker.cancel()
                self.sync_log.append("*** Sync cancelled by user ***")
                self.status_manager.end_task("Sync cancelled", 3000)

    def _set_sync_ui_state(self, enabled: bool):
        """Enable/disable UI elements during sync."""
        self.device_combo.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)
        self.playlist_list.setEnabled(enabled)
        self.select_all_btn.setEnabled(enabled)
        self.select_none_btn.setEnabled(enabled)
        self.sync_btn.setEnabled(enabled and len(self.selected_playlists) > 0)
        self.cancel_sync_btn.setVisible(not enabled)

    def _on_sync_progress(self, current: int, total: int, message: str):
        """Update progress display."""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)

        self.current_action.setText(message)

        # Update status with progress
        if total > 0:
            percentage = (current / total) * 100 if total > 0 else 0
            self.status_manager.show_message(
                f"Syncing: {message} ({percentage:.1f}%)", 0
            )
        else:
            self.status_manager.show_message(f"Syncing: {message}", 0)

    def _on_playlist_complete(self, result: Dict):
        """Handle completion of a single playlist sync."""
        status = "✅" if result["success"] else "❌"
        log_entry = f"{status} {result['playlist_name']}: {result['message']}"
        self.sync_log.append(log_entry)

        # Auto-scroll to bottom
        self.sync_log.verticalScrollBar().setValue(
            self.sync_log.verticalScrollBar().maximum()
        )

    def _on_sync_finished(self, results: List[Dict]):
        """Handle completion of all sync operations."""
        self._set_sync_ui_state(True)
        self.progress_bar.setVisible(False)

        successful = sum(1 for r in results if r["success"])
        total = len(results)
        total_tracks_copied = sum(r.get("tracks_copied", 0) for r in results)

        self.current_action.setText(
            f"Sync complete: {successful}/{total} playlists successful"
        )
        self.sync_log.append(f"\n=== Sync complete: {successful}/{total} playlists ===")
        self.sync_log.append(f"Total tracks copied: {total_tracks_copied}")

        # Update status manager
        if successful > 0:
            self.status_manager.end_task(
                f"Sync complete: {successful}/{total} playlists, {total_tracks_copied} tracks copied",
                5000,
            )
            QMessageBox.information(
                self,
                "Sync Complete",
                f"Sync completed!\n\n"
                f"Successful: {successful}/{total} playlists\n"
                f"Tracks copied: {total_tracks_copied}\n\n"
                f"Files are in: music/\n"
                f"Playlists are in: playlists/",
            )
        else:
            self.status_manager.end_task("Sync completed with no tracks copied", 3000)
            QMessageBox.warning(
                self,
                "Sync Complete",
                "No tracks were copied. Check if source files exist and destination is writable.",
            )

    def refresh(self):
        """Refresh the sync view (called when switching to this view)."""
        logger.debug("Sync view refreshed")
        # Optionally refresh devices and playlists when view becomes active
        self._refresh_devices()
        self._refresh_playlists()
