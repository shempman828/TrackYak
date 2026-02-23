from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from asset_paths import icon
from config_setup import app_config
from file_manager import FileOrganizer
from file_organizer_preview_dialog import OrganizationPreviewDialog
from import_dialog import ImportDialog
from logger_config import logger
from metadata_writer_dialog import show_metadata_write_dialog

# --- Long HTML texts extracted for clarity ---
_DIR_EXPLANATION = (
    "Set the root directory where your music library is stored. "
    "This is where file operations will be performed."
)

_TIPS_TEXT = (
    "• Backup your library before major changes<br>"
    "• Organization will move files on disk<br>"
    "• Metadata updates overwrite file tags"
)


class FileManager(QDialog):
    """Cleaner, modular FileManager dialog with modernized structure.

    Functional behavior is intentionally unchanged from the original version;
    this refactor focuses on readability, safety, and small UX improvements.
    """

    operation_complete = Signal(bool)
    library_modified = Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.root_path: Optional[Path] = None

        # worker references (plain Python references; guarded when used)
        self.organizer = None
        self.metadata_updater = None

        self.setWindowTitle("Library File Management")
        self.setMinimumSize(800, 520)

        # build UI
        self._init_ui()

        # load persisted root if available
        self._load_root_from_config()

    # -------------------- UI BUILDERS --------------------
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        main_layout.addWidget(self._build_card(self._build_dir_section()))
        main_layout.addWidget(self._build_card(self._build_organization_section()))
        main_layout.addWidget(self._build_card(self._build_metadata_section()))

        tips = self._build_tips_section()
        main_layout.addWidget(tips)

        main_layout.addStretch()

        # small style hooks (kept light)
        self.setStyleSheet("""
            QFrame.card { border-radius: 8px; padding: 10px; border: 1px solid #9385ea; }
            QPushButton[class='primary'] { padding: 6px 12px; border-radius: 6px; font-weight: 600; }
        """)

    def _build_card(self, widget) -> QFrame:
        """Wrap a widget in a subtle card frame for visual separation."""
        frame = QFrame()
        frame.setProperty("class", "card")
        frame.setFrameStyle(QFrame.Box)
        frame.setLineWidth(1)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(widget)
        return frame

    def _build_dir_section(self) -> QFrame:
        container = QFrame()
        layout = QVBoxLayout(container)

        header = QLabel("Library Location")
        header.setAlignment(Qt.AlignCenter)
        sub = QLabel(_DIR_EXPLANATION)
        sub.setWordWrap(True)

        control = QHBoxLayout()
        self.root_label = QLabel("Root Directory: <i>Not set</i>")
        self.root_label.setStyleSheet("color: #EAD685;")
        self.root_label.setWordWrap(True)

        btn_root = QPushButton("Set Root")
        btn_root.setToolTip("Choose the root folder for your music library")
        btn_root.setIcon(QIcon(icon("folder.svg")))
        btn_root.clicked.connect(self._set_root)
        btn_root.setProperty("class", "primary")

        control.addWidget(self.root_label, 1)
        control.addWidget(btn_root, 0)

        layout.addWidget(header)
        layout.addWidget(sub)
        layout.addLayout(control)

        return container

    def _build_organization_section(self) -> QFrame:
        container = QFrame()
        layout = QVBoxLayout(container)

        header = QLabel("File Organization")
        header.setAlignment(Qt.AlignCenter)
        explanation = QLabel(
            "Organizes your music files into a consistent folder structure:"
        )
        structure = QLabel("AlbumArtist/Album Title/Track# - Track Title.ext")
        structure.setStyleSheet("color: #EAD685;")
        explanation.setWordWrap(True)

        self.org_progress = QProgressBar()
        self.org_progress.hide()
        self.org_status = QLabel("Ready to organize files")

        btn_layout = QHBoxLayout()
        btn_organize = QPushButton("Analyze && Organize")
        btn_organize.setIcon(QIcon(icon("arrange.svg")))
        btn_organize.clicked.connect(self._organize_files)
        btn_organize.setProperty("class", "primary")

        self.btn_cancel_organize = QPushButton("Cancel")
        self.btn_cancel_organize.setIcon(QIcon(icon("minus.svg")))
        self.btn_cancel_organize.clicked.connect(self._cancel_organization)
        self.btn_cancel_organize.setEnabled(False)

        btn_layout.addWidget(btn_organize)
        btn_layout.addWidget(self.btn_cancel_organize)

        layout.addWidget(header)
        layout.addWidget(explanation)
        layout.addWidget(structure)
        layout.addWidget(self.org_status)
        layout.addWidget(self.org_progress)
        layout.addLayout(btn_layout)

        return container

    def _build_metadata_section(self) -> QFrame:
        container = QFrame()
        layout = QVBoxLayout(container)

        header = QLabel("Metadata Management")
        header.setAlignment(Qt.AlignCenter)
        explanation = QLabel(
            "Update embedded metadata (tags) to match the library database."
        )
        explanation.setWordWrap(True)

        self.metadata_progress = QProgressBar()
        self.metadata_progress.hide()
        self.metadata_status = QLabel("Ready to update metadata")

        btn_layout = QHBoxLayout()
        self.btn_update_metadata = QPushButton("Update Metadata")
        self.btn_update_metadata.setIcon(QIcon(icon("write.svg")))
        self.btn_update_metadata.clicked.connect(
            partial(show_metadata_write_dialog, self.controller, self)
        )
        self.btn_update_metadata.setProperty("class", "primary")

        self.btn_cancel_metadata = QPushButton("Cancel")
        self.btn_cancel_metadata.setIcon(QIcon(icon("minus.svg")))
        self.btn_cancel_metadata.clicked.connect(self._cancel_metadata_update)
        self.btn_cancel_metadata.setEnabled(False)

        btn_layout.addWidget(self.btn_update_metadata)
        btn_layout.addWidget(self.btn_cancel_metadata)

        layout.addWidget(header)
        layout.addWidget(explanation)
        layout.addWidget(self.metadata_status)
        layout.addWidget(self.metadata_progress)
        layout.addLayout(btn_layout)

        return container

    def _build_tips_section(self) -> QFrame:
        container = QFrame()
        layout = QVBoxLayout(container)
        header = QLabel("Usage Tips")
        header.setAlignment(Qt.AlignLeft)
        tips = QLabel(_TIPS_TEXT)
        tips.setWordWrap(True)
        layout.addWidget(header)
        layout.addWidget(tips)
        return container

    # -------------------- Helpers & Small Utilities --------------------
    def _reset_progress(
        self, bar: QProgressBar, label: QLabel, text: str = "Ready"
    ) -> None:
        bar.hide()
        bar.setValue(0)
        label.setText(text)

    def _worker_is_running(self, worker) -> bool:
        """Return True if worker exists and reports running state."""
        try:
            return (
                worker is not None
                and hasattr(worker, "isRunning")
                and worker.isRunning()
            )
        except Exception:
            return False

    def _cancel_worker(
        self,
        worker_ref,
        status_label: QLabel,
        progress: QProgressBar,
        cancel_button: QPushButton,
        cancel_message: str,
    ) -> None:
        """Safely cancel a worker if it's running, then cleanup UI and reference."""
        if self._worker_is_running(worker_ref):
            try:
                worker_ref.cancel()
                worker_ref.wait()
            except Exception as exc:  # defensive
                logger.warning(f"Error cancelling worker: {exc}")
            finally:
                try:
                    worker_ref.deleteLater()
                except Exception:
                    pass

        # common UI cleanup regardless of whether worker was running
        status_label.setText(cancel_message)
        self._reset_progress(progress, status_label, cancel_message)
        cancel_button.setEnabled(False)

        # if this was the organizer or metadata_updater, clear the attribute
        if worker_ref is self.organizer:
            self.organizer = None
        if worker_ref is self.metadata_updater:
            self.metadata_updater = None

    # -------------------- Organization Flow --------------------
    def _organize_files(self) -> None:
        """Start the two-phase file organization process with improved UX."""
        if not self._validate_requirements():
            return

        # show indeterminate progress while analyzing (modern feel)
        self.org_progress.show()
        self.org_progress.setRange(0, 0)  # indeterminate
        self.org_status.setText("Analyzing files…")
        self.btn_cancel_organize.setEnabled(True)

        # Create organizer and keep a single reference to it
        organizer = FileOrganizer(root=self.root_path, controller=self.controller)
        self.organizer = organizer

        organizer.progress_updated.connect(self._update_organization_progress)
        organizer.analysis_complete.connect(self._show_organization_preview)
        organizer.finished.connect(self._organization_complete)
        # call cleanup handler after finished to deleteLater and clear reference
        organizer.finished.connect(self._on_organizer_finished)
        organizer.cleanup_progress.connect(self._update_cleanup_progress)

        organizer.start()

    def _update_organization_progress(self, percent: int, current_file: str) -> None:
        # switch to determinate mode when we have a percent value
        if self.org_progress.maximum() == 0:
            self.org_progress.setRange(0, 100)
        self.org_progress.setValue(percent)
        self.org_status.setText(f"{current_file} — {percent}%")

    def _update_cleanup_progress(self, percent: int, current_dir: str) -> None:
        # map cleanup into last few percents if desired
        if self.org_progress.maximum() == 0:
            self.org_progress.setRange(0, 100)
        # keep it simple: show percent as-is
        self.org_progress.setValue(min(100, 95 + percent // 20))
        self.org_status.setText(f"Cleaning: {current_dir}")

    def _show_organization_preview(
        self, auto_ops: List[Dict], confirm_ops: List[Dict]
    ) -> None:
        # if nothing to do, short-circuit
        if not auto_ops and not confirm_ops:
            QMessageBox.information(
                self, "No Changes Needed", "All files are already organized correctly!"
            )
            if self.organizer and hasattr(self.organizer, "user_cancelled"):
                try:
                    self.organizer.user_cancelled()
                except Exception:
                    pass
            self._organization_complete(True, 0)
            return

        preview_dialog = OrganizationPreviewDialog(self, auto_ops, confirm_ops)
        result = preview_dialog.exec_()

        if result == QDialog.Accepted:
            approved_ops = preview_dialog.get_approved_operations()
            if approved_ops:
                if self.organizer and hasattr(self.organizer, "user_approval_received"):
                    self.organizer.user_approval_received(approved_ops)
                self.org_status.setText(f"Executing {len(approved_ops)} operations...")
                # organizer will continue and emit finished
            else:
                # user accepted but approved no ops
                if self.organizer and hasattr(self.organizer, "user_cancelled"):
                    self.organizer.user_cancelled()
                self._organization_complete(True, 0)
        else:
            if self.organizer and hasattr(self.organizer, "user_cancelled"):
                self.organizer.user_cancelled()
            self._organization_complete(False, 0)

    def _on_organizer_finished(self, *args, **kwargs) -> None:
        # Cleanup organizer reference after finished
        try:
            if self.organizer:
                self.organizer.deleteLater()
        except Exception:
            pass
        finally:
            self.organizer = None

    def _organization_complete(self, success: bool, files_moved: int) -> None:
        self._reset_progress(
            self.org_progress, self.org_status, "Ready to organize files"
        )
        self.btn_cancel_organize.setEnabled(False)

        if success:
            if files_moved > 0:
                self.org_status.setText(f"Complete — moved {files_moved} files")
                QMessageBox.information(
                    self,
                    "Organization Complete",
                    f"Successfully organized {files_moved} files",
                )
                self.library_modified.emit()
            else:
                self.org_status.setText("No files needed organization")
        else:
            self.org_status.setText("Organization cancelled")
            QMessageBox.warning(self, "Cancelled", "File organization was cancelled")

    def _cancel_organization(self) -> None:
        self._cancel_worker(
            self.organizer,
            self.org_status,
            self.org_progress,
            self.btn_cancel_organize,
            "Organization cancelled",
        )

    # -------------------- Metadata Flow --------------------
    def _update_metadata_progress(self, percent: int, current_file: str) -> None:
        if self.metadata_progress.maximum() == 0:
            self.metadata_progress.setRange(0, 100)
        self.metadata_progress.setValue(percent)
        self.metadata_status.setText(f"{current_file} — {percent}%")

    def _metadata_update_complete(self, success_count: int, total_count: int) -> None:
        self._reset_metadata_ui()
        self.metadata_status.setText(
            f"Complete — {success_count}/{total_count} files updated"
        )

        QMessageBox.information(
            self,
            "Metadata Update Complete",
            f"Updated {success_count} out of {total_count} files",
        )

        if success_count > 0:
            self.library_modified.emit()

    def _cancel_metadata_update(self) -> None:
        self._cancel_worker(
            self.metadata_updater,
            self.metadata_status,
            self.metadata_progress,
            self.btn_cancel_metadata,
            "Metadata update cancelled",
        )

    def _reset_metadata_ui(self) -> None:
        self.btn_update_metadata.setEnabled(True)
        self.btn_cancel_metadata.setEnabled(False)
        self.metadata_progress.setValue(0)
        self.metadata_progress.hide()

    # -------------------- Config / Path Helpers --------------------
    def _load_root_from_config(self) -> None:
        try:
            if app_config:
                root_path = app_config.get_base_directory()
                if root_path and Path(root_path).exists():
                    self.root_path = Path(root_path)
                    self.root_label.setText(f"Root Directory: {self.root_path}")
                    logger.info(f"Loaded root directory from config: {self.root_path}")
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Could not load root directory from config: {e}")

    def _save_root_to_config(self) -> None:
        try:
            if self.root_path and app_config:
                app_config.set_base_directory(self.root_path)
                app_config.save()
                logger.info(f"Saved root directory to config: {self.root_path}")
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Could not save root directory to config: {e}")

    def _set_root(self) -> None:
        try:
            current_dir = (
                app_config.get_base_directory() if app_config else str(Path.home())
            )
            picked = QFileDialog.getExistingDirectory(
                self, "Select Root Directory", str(current_dir)
            )
            if picked:
                chosen = Path(picked).resolve()
                if not chosen.exists():
                    raise FileNotFoundError(f"Path {chosen} does not exist")
                self.root_path = chosen
                self.root_label.setText(f"Root Directory: {self.root_path}")
                self._save_root_to_config()
                logger.info(f"Root directory set to: {self.root_path}")
        except Exception as e:
            self._show_error("Directory Error", f"Failed to set root directory:\n{e}")

    # -------------------- Validation / Dialogs --------------------
    def _validate_requirements(self) -> bool:
        if not self.root_path:
            self._show_warning("Requirements Missing", "Root directory must be set")
            return False
        return True

    def _show_import_dialog(self) -> None:
        dlg = ImportDialog(self.controller)
        dlg.exec_()

    # -------------------- Standardized Alerts --------------------
    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
        logger.warning(f"{title}: {message}")

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        logger.error(f"{title}: {message}")
