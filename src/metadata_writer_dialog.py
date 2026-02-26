"""
PySide6 dialog for writing database metadata to audio files.
Streamlined version - entire library updates only.
"""

import os
from typing import Dict, List, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from src.metadata_writer import MetadataWriter, WriteMode
from src.status_utility import StatusManager


class MetadataScannerWorker(QThread):
    """Worker thread that collects tracks eligible for a metadata write.

    Emits finished with {track_id: bool} where True = has a valid file
    and should be written.  The dialog then passes all True IDs straight
    to the write worker — no diff comparison, always write if data exists.
    """

    progress = Signal(int, int)  # current, total
    finished = Signal(dict)  # {track_id: bool}  True = eligible
    log_message = Signal(str)

    def __init__(self, metadata_writer, parent=None):
        super().__init__(parent)
        self.metadata_writer = metadata_writer
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            tracks = self.metadata_writer.controller.get.get_all_entities("Track")
            total = len(tracks)
            results = {}

            self.log_message.emit(f"Scanning {total} tracks for writable files...")

            for i, track in enumerate(tracks):
                if self._is_cancelled:
                    break

                self.progress.emit(i + 1, total)

                try:
                    eligible = bool(
                        track.track_file_path and os.path.exists(track.track_file_path)
                    )
                    results[track.track_id] = eligible

                    if i % 100 == 0 or i == total - 1:
                        self.log_message.emit(f"Scanned {i + 1}/{total} tracks...")

                except Exception as e:
                    self.log_message.emit(
                        f"Error scanning track {track.track_id}: {str(e)}"
                    )
                    results[track.track_id] = False

            eligible_count = sum(1 for v in results.values() if v)
            self.log_message.emit(
                f"Scan complete: {eligible_count}/{total} tracks have writable files."
            )
            self.finished.emit(results)

        except Exception as e:
            self.log_message.emit(f"Scan failed: {str(e)}")
            self.finished.emit({})


class MetadataWriteWorker(QThread):
    """Worker thread for metadata writing operations."""

    progress = Signal(int, int, int)  # current, total, track_id
    finished = Signal(dict)  # results: {track_id: success}
    log_message = Signal(str)

    def __init__(
        self,
        metadata_writer: MetadataWriter,
        track_ids: List[int],
        mode: WriteMode,
        parent=None,
    ):
        super().__init__(parent)
        self.metadata_writer = metadata_writer
        self.track_ids = track_ids
        self.mode = mode
        self._is_cancelled = False

    def cancel(self):
        """Cancel the operation."""
        self._is_cancelled = True

    def run(self):
        """Execute the metadata writing operation."""
        total = len(self.track_ids)
        results = {}

        for i, track_id in enumerate(self.track_ids):
            if self._is_cancelled:
                break

            self.progress.emit(i + 1, total, track_id)

            try:
                success = self.metadata_writer.write_metadata_to_track(
                    track_id, self.mode
                )
                results[track_id] = success

                if success:
                    self.log_message.emit(f"✓ Updated track {track_id}")
                else:
                    self.log_message.emit(f"✗ Failed to update track {track_id}")

            except Exception as e:
                self.log_message.emit(f"✗ Error updating track {track_id}: {str(e)}")
                results[track_id] = False

        self.finished.emit(results)


class MetadataWriteDialog(QDialog):
    """Streamlined dialog for writing metadata to all audio files."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.metadata_writer = MetadataWriter(controller)
        self.status_manager = StatusManager
        self.scanner_thread: Optional[MetadataScannerWorker] = None
        self.writer_thread: Optional[MetadataWriteWorker] = None
        self.scan_results: Dict[
            int, tuple
        ] = {}  # track_id: (needs_update, diff_summary)

        self.setWindowTitle("Update Audio File Metadata")
        self.setMinimumSize(600, 500)
        self.init_ui()

    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        # Header
        header_label = QLabel("Sync all audio files with database metadata")
        header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(header_label)

        # Write mode selection
        mode_group = QGroupBox("Write Mode")
        mode_layout = QVBoxLayout(mode_group)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Add missing tags only (safe)", WriteMode.ADD_ONLY)
        self.mode_combo.addItem(
            "Update existing tags (recommended)", WriteMode.UPDATE_EXISTING
        )
        self.mode_combo.addItem(
            "Replace all tags (overwrites everything)", WriteMode.REPLACE_ALL
        )
        self.mode_combo.setCurrentIndex(1)  # Default to UPDATE_EXISTING

        mode_layout.addWidget(QLabel("How should metadata be written?"))
        mode_layout.addWidget(self.mode_combo)

        # Dry run option
        self.dry_run_check = QCheckBox("Preview only (don't write files)")
        mode_layout.addWidget(self.dry_run_check)

        layout.addWidget(mode_group)

        # Progress section (initially hidden)
        self.progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(self.progress_group)

        self.progress_label = QLabel("Ready")
        self.progress_bar = QProgressBar()

        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)

        self.progress_group.setVisible(False)
        layout.addWidget(self.progress_group)

        # Action buttons
        button_layout = QHBoxLayout()

        self.scan_btn = QPushButton("Scan Library")
        self.scan_btn.clicked.connect(self.start_scan)

        self.update_btn = QPushButton("Update All Files")
        self.update_btn.clicked.connect(self.start_update)
        self.update_btn.setEnabled(False)
        self.update_btn.setStyleSheet("font-weight: bold;")

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_operation)
        self.cancel_btn.setEnabled(False)

        button_layout.addWidget(self.scan_btn)
        button_layout.addWidget(self.update_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        # Log output
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        font = QFont("Courier New", 9)
        self.log_output.setFont(font)

        log_layout.addWidget(self.log_output)
        layout.addWidget(log_group)

        # Status bar
        self.status_label = QLabel("Ready to scan library")
        layout.addWidget(self.status_label)

    def log_message(self, message: str):
        """Add a message to the log."""
        self.log_output.append(message)

    def update_status(self, message: str):
        """Update the status label."""
        self.status_label.setText(message)

    def start_scan(self):
        """Start scanning the library for metadata differences."""
        # Disable UI during scan
        self.scan_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_group.setVisible(True)

        # Start status manager task
        self.status_manager.start_task("Scanning library for metadata differences")

        # Clear previous results
        self.scan_results = {}

        # Start scanner thread
        self.scanner_thread = MetadataScannerWorker(self.metadata_writer)
        self.scanner_thread.progress.connect(self.update_scan_progress)
        self.scanner_thread.finished.connect(self.on_scan_finished)
        self.scanner_thread.log_message.connect(self.log_message)
        self.scanner_thread.start()

        self.log_message("=== Starting library scan ===")
        self.update_status("Scanning library for metadata differences...")

    def update_scan_progress(self, current: int, total: int):
        """Update scan progress display."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Scanning: {current}/{total} tracks")

    def on_scan_finished(self, results):
        """Handle completion of library scan.

        results is {track_id: bool} — True means the file exists and should
        be written.  No tuple unpacking needed.
        """
        self.scan_results = results

        eligible_ids = [tid for tid, ok in results.items() if ok]
        eligible_count = len(eligible_ids)
        total_count = len(results)

        if eligible_count > 0:
            self.status_manager.end_task(
                f"Found {eligible_count} files to update", 5000
            )
            self.update_btn.setEnabled(True)
            self.update_status(f"Ready to write metadata for {eligible_count} files")
            self.tracks_to_update = eligible_ids
        else:
            self.status_manager.end_task("No writable files found", 5000)
            self.update_btn.setEnabled(False)
            self.update_status("No tracks with valid file paths found")
            self.tracks_to_update = []

        self.scan_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_group.setVisible(False)

        self.log_message(
            f"=== Scan complete: {eligible_count}/{total_count} files eligible for update ==="
        )

    def start_update(self):
        """Start updating metadata for all tracks that need it."""
        if not hasattr(self, "tracks_to_update") or not self.tracks_to_update:
            QMessageBox.warning(
                self, "No Updates Needed", "No files need metadata updates."
            )
            return

        mode = self.mode_combo.currentData()
        track_ids = self.tracks_to_update

        if self.dry_run_check.isChecked():
            self.log_message("=== DRY RUN - No files will be modified ===")
            self.log_message(
                f"Would update {len(track_ids)} files with mode: {mode.name}"
            )
            self.update_status(
                f"Dry run complete - would update {len(track_ids)} files"
            )
            self.status_manager.show_message("Dry run complete", 3000)
            return

        # Disable UI during operation
        self.scan_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_group.setVisible(True)

        # Start status manager task
        self.status_manager.start_task(f"Updating {len(track_ids)} files")

        # Start writer thread
        self.writer_thread = MetadataWriteWorker(self.metadata_writer, track_ids, mode)
        self.writer_thread.progress.connect(self.update_write_progress)
        self.writer_thread.finished.connect(self.on_update_finished)
        self.writer_thread.log_message.connect(self.log_message)
        self.writer_thread.start()

        self.log_message(f"=== Starting metadata update for {len(track_ids)} files ===")
        self.update_status(f"Updating {len(track_ids)} files...")

    def update_write_progress(self, current: int, total: int, track_id: int):
        """Update write progress display."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(
            f"Updating: {current}/{total} (Track ID: {track_id})"
        )

    def on_update_finished(self, results: Dict[int, bool]):
        """Handle completion of metadata update."""
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)

        # Update status manager
        if success_count == total_count:
            self.status_manager.end_task(
                f"Successfully updated all {total_count} files", 5000
            )
        else:
            self.status_manager.end_task(
                f"Updated {success_count}/{total_count} files", 5000
            )

        # Re-enable UI
        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_group.setVisible(False)

        # Show results
        self.log_message(
            f"=== Update complete: {success_count}/{total_count} successful ==="
        )
        self.update_status(f"Updated {success_count}/{total_count} files successfully")

        if success_count == total_count:
            QMessageBox.information(
                self,
                "Success",
                f"Successfully updated metadata for all {total_count} files",
            )
        else:
            QMessageBox.warning(
                self,
                "Completed with Errors",
                f"Updated {success_count} files successfully, {total_count - success_count} failed",
            )

    def cancel_operation(self):
        """Cancel the current operation (scan or write)."""
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
            self.scanner_thread.wait(3000)
            self.log_message("Scan cancelled")
            self.status_manager.end_task("Scan cancelled", 3000)
        elif self.writer_thread and self.writer_thread.isRunning():
            self.writer_thread.cancel()
            self.writer_thread.wait(3000)
            self.log_message("Update cancelled")
            self.status_manager.end_task("Update cancelled", 3000)

        # Reset UI
        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(len(self.scan_results) > 0)
        self.cancel_btn.setEnabled(False)
        self.progress_group.setVisible(False)
        self.update_status("Operation cancelled")

    def closeEvent(self, event):
        """Handle dialog close event."""
        # Cancel any running operations
        self.cancel_operation()
        event.accept()


def show_metadata_write_dialog(controller, parent=None):
    """Convenience function to show the metadata write dialog."""
    dialog = MetadataWriteDialog(controller, parent)
    dialog.exec()
