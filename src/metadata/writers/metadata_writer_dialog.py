"""Dialog for writing database metadata to the library's audio files."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager, show_status_message
from src.metadata.metadata_writer import MetadataWriter, WriteMode


class MetadataScannerWorker(CancellableWorker):
    """Collects the tracks that have a file to write: needs_tag_write tracks, or all with full_rescan."""

    progress = Signal(int, int)  # current, total
    # object, not dict: PySide marshals a queued dict signal via QVariantMap,
    # which only supports string keys and fails to copy-convert this
    # int-keyed {track_id: bool} payload across the thread boundary.
    finished = Signal(object)  # {track_id: bool}  True = eligible
    log_message = Signal(str)

    def __init__(self, metadata_writer, full_rescan: bool = False, parent=None):
        """Keep the writer (for its controller) and the scan scope."""
        super().__init__(parent)
        self.metadata_writer = metadata_writer
        self.full_rescan = full_rescan

    def cancel(self):
        """Request cancellation; the scan stops before the next track."""
        self.request_cancel()

    def run(self):
        """Scan the tracks and emit finished with {track_id: has_writable_file}."""
        try:
            controller = self.metadata_writer.controller
            tracks = controller.get.get_all_entities("Track") if self.full_rescan else controller.get.get_all_entities("Track", needs_tag_write=1)
            total = len(tracks)
            results = {}

            scan_kind = "full library" if self.full_rescan else "dirty (changed)"
            self.log_message.emit(f"Scanning {total} {scan_kind} tracks for writable files...")

            for i, track in enumerate(tracks):
                if self.is_cancelled:
                    break

                self.progress.emit(i + 1, total)

                try:
                    eligible = bool(track.track_file_path and Path(track.track_file_path).exists())
                    results[track.track_id] = eligible

                    if i % 100 == 0 or i == total - 1:
                        self.log_message.emit(f"Scanned {i + 1}/{total} tracks...")

                except (SQLAlchemyError, AttributeError, RuntimeError) as e:
                    logger.error(f"Error scanning track {track.track_id}: {e}")
                    self.log_message.emit(f"Error scanning track {track.track_id}: {e!s}")
                    results[track.track_id] = False

            eligible_count = sum(1 for v in results.values() if v)
            self.log_message.emit(f"Scan complete: {eligible_count}/{total} tracks have writable files.")
            self.finished.emit(results)

        except Exception as e:
            # Intentional broad boundary catch: this is a QThread's run() -
            # an unhandled exception here would kill the thread silently
            # instead of surfacing to the UI, so it must never propagate.
            logger.exception("Library metadata scan failed")
            self.log_message.emit(f"Scan failed: {e!s}")
            self.finished.emit({})
        finally:
            # get_all_entities is read-only and nothing else here commits --
            # see CancellableWorker's docstring.
            self._release_db_session()


class MetadataWriteWorker(CancellableWorker):
    """Writes database metadata to each track's file, in order."""

    progress = Signal(int, int, int)  # current, total, track_id
    # object, not dict: see MetadataScannerWorker.finished for why a
    # queued int-keyed dict signal fails to copy-convert.
    finished = Signal(object)  # results: {track_id: success}
    log_message = Signal(str)

    def __init__(self, metadata_writer: MetadataWriter, track_ids: list[int], mode: WriteMode, parent=None):
        """Keep the writer, the tracks to write, and the write mode."""
        super().__init__(parent)
        self.metadata_writer = metadata_writer
        self.track_ids = track_ids
        self.mode = mode

    def cancel(self):
        """Request cancellation; the write stops after the current file."""
        self.request_cancel()

    def run(self):
        """Write each track and emit finished with {track_id: success} for the tracks attempted."""
        total = len(self.track_ids)
        results = {}

        try:
            for i, track_id in enumerate(self.track_ids):
                if self.is_cancelled:
                    break

                self.progress.emit(i + 1, total, track_id)

                try:
                    success = self.metadata_writer.write_metadata_to_track(track_id, self.mode)
                except Exception as e:
                    # Intentional broad boundary catch: one bad file must not end the run or kill the thread.
                    logger.exception(f"Error updating track {track_id}")
                    self.log_message.emit(f"✗ Error updating track {track_id}: {e!s}")
                    success = False
                else:
                    self.log_message.emit(f"✓ Updated track {track_id}" if success else f"✗ Failed to update track {track_id}")
                results[track_id] = success
        finally:
            self._release_db_session()
            # Always emitted, so the dialog never stays locked.
            self.finished.emit(results)


class MetadataWriteDialog(QDialog):
    """Scans for tracks to write, then writes database metadata to their audio files."""

    def __init__(self, controller, parent=None):
        """Build the dialog for the given database controller."""
        super().__init__(parent)
        self.controller = controller
        self.metadata_writer = MetadataWriter(controller)
        self.status_manager = StatusManager
        self.scanner_thread: MetadataScannerWorker | None = None
        self.writer_thread: MetadataWriteWorker | None = None
        self.scan_results: dict[int, bool] = {}  # track_id -> has a writable file
        self.tracks_to_update: list[int] = []
        self._cancelled = False

        self.setWindowTitle("Update Audio File Metadata")
        self.setMinimumSize(600, 500)
        self.init_ui()

    def init_ui(self):
        """Build the widgets."""
        layout = QVBoxLayout(self)

        header_label = QLabel("Write database metadata to audio files")
        header_label.setProperty("title", True)
        layout.addWidget(header_label)

        # Write mode selection
        mode_group = QGroupBox("Write Mode")
        mode_layout = QVBoxLayout(mode_group)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Add missing tags only (safe)", WriteMode.ADD_ONLY)
        self.mode_combo.addItem("Update existing tags (recommended)", WriteMode.UPDATE_EXISTING)
        self.mode_combo.addItem("Replace all tags (keeps embedded artwork)", WriteMode.REPLACE_ALL)
        self.mode_combo.setCurrentIndex(1)  # Default to UPDATE_EXISTING

        mode_layout.addWidget(QLabel("How should metadata be written?"))
        mode_layout.addWidget(self.mode_combo)

        # Dry run option
        self.dry_run_check = QCheckBox("Preview only (don't write files)")
        mode_layout.addWidget(self.dry_run_check)

        # Full rescan option — by default, scanning only considers tracks the
        # database already knows changed (Track.needs_tag_write); this is the
        # manual fallback for reconciling files edited outside the app.
        self.full_rescan_check = QCheckBox("Full rescan (check every track, ignoring the changed-tracks flag)")
        mode_layout.addWidget(self.full_rescan_check)

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
        self.update_btn.setDefault(True)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_operation)
        self.cancel_btn.setEnabled(False)

        button_layout.addWidget(self.scan_btn)
        button_layout.addWidget(self.update_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        log_layout.addWidget(self.log_output)
        layout.addWidget(log_group, 1)  # the log takes the spare height

        # Status bar
        self.status_label = QLabel("Ready to scan library")
        layout.addWidget(self.status_label)

    def log_message(self, message: str):
        """Append a message to the log."""
        self.log_output.append(message)

    def update_status(self, message: str):
        """Update the status label."""
        self.status_label.setText(message)

    def start_scan(self):
        """Start the scan worker."""
        self._cancelled = False
        self.scan_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_group.setVisible(True)

        self.status_manager.start_task("Scanning library for tracks to write")
        self.scan_results = {}
        self.tracks_to_update = []

        self.scanner_thread = MetadataScannerWorker(self.metadata_writer, full_rescan=self.full_rescan_check.isChecked())
        self.scanner_thread.progress.connect(self.update_scan_progress)
        self.scanner_thread.finished.connect(self.on_scan_finished)
        self.scanner_thread.log_message.connect(self.log_message)
        self.scanner_thread.start()

        scan_kind = "full library" if self.full_rescan_check.isChecked() else "dirty"
        self.log_message(f"=== Starting {scan_kind} scan ===")
        self.update_status(f"Scanning {scan_kind} tracks for writable files...")

    def update_scan_progress(self, current: int, total: int):
        """Show scan progress."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Scanning: {current}/{total} tracks")

    def on_scan_finished(self, results):
        """Enable Update for the tracks the scan found, unless the scan was cancelled."""
        if self._cancelled:
            # Partial results after a cancel are not a complete list of tracks to write.
            self.scan_results = {}
            self.tracks_to_update = []
            self.update_btn.setEnabled(False)
            self.scan_btn.setEnabled(True)
            self.progress_group.setVisible(False)
            return
        self.scan_results = results
        scan_kind = "full library" if self.full_rescan_check.isChecked() else "dirty"

        eligible_ids = [tid for tid, ok in results.items() if ok]
        eligible_count = len(eligible_ids)
        total_count = len(results)

        if eligible_count > 0:
            self.status_manager.end_task(f"Found {eligible_count} files to update", 5000)
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

        logger.info(f"Metadata scan complete ({scan_kind}): {eligible_count}/{total_count} files eligible for update")
        self.log_message(f"=== Scan complete ({scan_kind}): {eligible_count}/{total_count} files eligible for update ===")

    def start_update(self):
        """Start the write worker for the scanned tracks (or log a dry run)."""
        if not self.tracks_to_update:
            show_status_message(self, "No files need metadata updates.")
            return

        mode = self.mode_combo.currentData()
        track_ids = self.tracks_to_update

        if self.dry_run_check.isChecked():
            self.log_message("=== DRY RUN - No files will be modified ===")
            self.log_message(f"Would update {len(track_ids)} files with mode: {mode.name}")
            self.update_status(f"Dry run complete - would update {len(track_ids)} files")
            self.status_manager.show_message("Dry run complete", 3000)
            return

        self._cancelled = False
        self.scan_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_group.setVisible(True)

        self.status_manager.start_task(f"Updating {len(track_ids)} files")

        self.writer_thread = MetadataWriteWorker(self.metadata_writer, track_ids, mode)
        self.writer_thread.progress.connect(self.update_write_progress)
        self.writer_thread.finished.connect(self.on_update_finished)
        self.writer_thread.log_message.connect(self.log_message)
        self.writer_thread.start()

        self.log_message(f"=== Starting metadata update for {len(track_ids)} files ===")
        self.update_status(f"Updating {len(track_ids)} files...")

    def update_write_progress(self, current: int, total: int, track_id: int):
        """Show write progress."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Updating: {current}/{total} (Track ID: {track_id})")

    def on_update_finished(self, results: dict[int, bool]):
        """Show the write summary; a cancelled run reports how many files were written before the stop."""
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)

        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_group.setVisible(False)

        if self._cancelled:
            planned = len(self.tracks_to_update)
            logger.info(f"Metadata update cancelled: {success_count}/{planned} files updated")
            self.log_message(f"=== Update cancelled: {success_count} of {planned} files updated ===")
            self.update_status(f"Cancelled - {success_count} of {planned} files updated")
            # The remaining tracks keep needs_tag_write, so the next scan finds them again.
            self.tracks_to_update = []
            return

        if success_count == total_count:
            self.status_manager.end_task(f"Successfully updated all {total_count} files", 5000)
        else:
            self.status_manager.end_task(f"Updated {success_count}/{total_count} files", 5000)

        logger.info(f"Metadata update complete: {success_count}/{total_count} files updated successfully")
        self.log_message(f"=== Update complete: {success_count}/{total_count} successful ===")
        self.update_status(f"Updated {success_count}/{total_count} files successfully")

        if success_count == total_count:
            show_status_message(self, f"Successfully updated metadata for all {total_count} files")
        else:
            QMessageBox.warning(self, "Completed with Errors", f"Updated {success_count} files successfully, {total_count - success_count} failed")

    def cancel_operation(self):
        """Cancel the running scan or write, if any."""
        if self.scanner_thread and self.scanner_thread.isRunning():
            self._cancelled = True
            self.scanner_thread.cancel()
            self.log_message("Scan cancelled")
            self.status_manager.end_task("Scan cancelled", 3000)
            self.update_status("Scan cancelled")
        elif self.writer_thread and self.writer_thread.isRunning():
            self._cancelled = True
            self.writer_thread.cancel()
            self.log_message("Cancelling - finishing the current file...")
            self.status_manager.end_task("Update cancelled", 3000)
            self.update_status("Cancelling...")
        else:
            return
        self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        """Cancel any running work and wait for the worker, so no QThread outlives the dialog."""
        self.cancel_operation()
        for worker in (self.scanner_thread, self.writer_thread):
            if worker and worker.isRunning():
                # Each loop checks the cancel flag per track, so this returns after the current file.
                worker.wait()
        event.accept()


def show_metadata_write_dialog(controller, parent=None):
    """Show the metadata write dialog modally."""
    dialog = MetadataWriteDialog(controller, parent)
    dialog.exec()
