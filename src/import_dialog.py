import json
from pathlib import Path
from typing import List, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
)

from src.config_setup import app_config
from src.library_import import ImportWorker
from src.asset_paths import config, icon
from src.logger_config import logger
from src.status_utility import StatusManager

CONFIG_FILE = config("import_paths.json")
SUPPORTED_FORMATS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a"}


class ImportDialog(QDialog):
    """Dialog for managing music file imports with enhanced UX"""

    progress_updated = Signal(int, int)
    import_completed = Signal(int)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.directories: List[Tuple[Path, bool]] = []
        self.setMinimumSize(800, 420)
        self.setWindowTitle("Import Music Files")
        self.import_worker = None
        self._init_ui()
        self.load_saved_directories()

    def _init_ui(self):
        """Initialize UI with modern layout"""
        layout = QVBoxLayout()

        # Directory list
        self.dir_list = QListWidget()
        self.dir_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.dir_list.itemChanged.connect(self._save_directories)
        self.dir_list.itemDoubleClicked.connect(
            lambda item: item.setCheckState(
                Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            )
        )

        # Buttons
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("Add Directory")
        btn_add.setIcon(QIcon(icon("plus.svg")))
        btn_add.clicked.connect(self._add_directory)
        btn_remove = QPushButton("Remove Selected")
        btn_remove.setIcon(QIcon(icon("minus.svg")))
        btn_remove.clicked.connect(self._remove_directories)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_remove)
        btn_layout.addSpacerItem(
            QSpacerItem(20, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )

        # Start/Cancel buttons
        action_layout = QHBoxLayout()
        self.btn_scan = QPushButton("Start Import")
        self.btn_scan.clicked.connect(self._start_import)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setIcon(QIcon(icon("cancel.svg")))
        self.btn_cancel.clicked.connect(self.cancel_import)
        self.btn_cancel.setEnabled(False)
        action_layout.addWidget(self.btn_scan)
        action_layout.addWidget(self.btn_cancel)
        action_layout.addSpacerItem(
            QSpacerItem(20, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )

        # Progress
        self.status_note = QLabel(
            "You can close this window; the import will continue in the background."
        )
        self.status_note.setWordWrap(True)
        self.status_note.setStyleSheet("font-style: italic;")
        self.status_note.hide()

        # Assemble layout
        layout.addWidget(QLabel("Tracked Directories:"))
        layout.addWidget(self.dir_list)
        layout.addLayout(btn_layout)
        layout.addLayout(action_layout)
        layout.addWidget(self.status_note)
        self.setLayout(layout)

        # Track if user requested close during import
        self._close_requested = False

    def _handle_progress_update(self, current: int, total: int):
        """Handle progress update from ImportWorker"""
        logger.debug(f"DEBUG: ImportDialog progress: {current}/{total}")
        if total > 0:
            percent = int(current / total * 100)
            StatusManager.show_message(
                f"Importing: {current}/{total} files ({percent}%)", 0
            )
        else:
            # Indeterminate progress
            StatusManager.show_message("Scanning files...", 0)

    def _add_directory(self):
        """Add directory with duplicate checking"""
        try:
            default_dir = str(app_config.get_base_directory())
            path = QFileDialog.getExistingDirectory(
                self, "Select Music Directory", default_dir
            )
            if path:
                path_obj = Path(path).resolve()
                if any(p == path_obj for p, _ in self.directories):
                    raise ValueError("Directory already in list")
                item = QListWidgetItem(str(path_obj))
                item.setCheckState(Qt.Checked)
                self.dir_list.addItem(item)
                self.directories.append((path_obj, True))
                self._save_directories()
                logger.info(f"Added import directory: {path_obj}")
        except Exception as e:
            self._show_error("Add Error", str(e))

    def _remove_directories(self):
        """Remove selected directories"""
        try:
            for item in reversed(self.dir_list.selectedItems()):
                path = Path(item.text())
                self.directories = [(p, s) for p, s in self.directories if p != path]
                self.dir_list.takeItem(self.dir_list.row(item))
            self._save_directories()
        except Exception as e:
            self._show_error("Remove Error", str(e))

    def _start_import(self):
        """Start import for checked directories."""
        try:
            if self.import_worker and self.import_worker.isRunning():
                raise RuntimeError("Import already in progress")

            StatusManager.start_task("Importing music files...")
            self.btn_scan.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.status_note.show()

            selected_paths = [
                str(Path(self.dir_list.item(i).text()))
                for i in range(self.dir_list.count())
                if self.dir_list.item(i).checkState() == Qt.Checked
            ]
            if not selected_paths:
                raise ValueError("No directories selected")

            self.import_worker = ImportWorker(self.controller, selected_paths)
            # Connect signals directly to handlers
            self.import_worker.progress.connect(self._handle_progress_update)
            self.import_worker.finished.connect(self._import_complete)
            self.import_worker.start()

        except Exception as e:
            self._show_error("Import Error", str(e))
            self.btn_scan.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.status_note.hide()

    def _import_complete(self, success_count: int):
        """Handle import completion."""
        StatusManager.end_task(
            f"Import complete: {success_count} files processed", 3000
        )
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.status_note.hide()
        self.import_completed.emit(success_count)

        # If import completed and user had requested close, close now
        if self._close_requested:
            logger.info("Import completed, closing dialog as requested")
            super().accept()
            return

        # Show completion message only if dialog is visible
        if self.isVisible():
            QMessageBox.information(
                self, "Complete", f"Import completed. Processed {success_count} files"
            )
        else:
            # Dialog was hidden, show notification and keep hidden
            # Optionally, you could automatically show the dialog here
            logger.info(f"Import completed in background: {success_count} files")
            StatusManager.show_message(f"Import completed: {success_count} files", 5000)

    def cancel_import(self):
        """Cancel ongoing import operation"""
        if self.import_worker and self.import_worker.isRunning():
            self.import_worker.stop()
            self.import_worker.wait()
            StatusManager.end_task("Import cancelled", 3000)
            self.btn_scan.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.status_note.hide()

    def load_saved_directories(self):
        """Load directories from config but respect current UI state"""
        try:
            if Path(CONFIG_FILE).exists():
                with open(CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                    if not self.directories:
                        self.directories = [(Path(p), s) for p, s in saved]
                    self._refresh_list()
        except Exception as e:
            logger.error(f"Config load error: {e}")

    def _save_directories(self):
        """Save current checkbox states to config"""
        try:
            data = [
                (
                    str(Path(self.dir_list.item(i).text()).resolve()),
                    self.dir_list.item(i).checkState() == Qt.Checked,
                )
                for i in range(self.dir_list.count())
            ]
            temp_file = Path(CONFIG_FILE).with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=4)
            temp_file.replace(CONFIG_FILE)
            self.directories = [(Path(p), s) for p, s in data]
        except Exception as e:
            logger.error(f"Config save error: {e}")

    def _refresh_list(self):
        scroll_pos = self.dir_list.verticalScrollBar().value()
        selected_paths = [item.text() for item in self.dir_list.selectedItems()]
        self.dir_list.clear()
        for path, checked in self.directories:
            item = QListWidgetItem(str(path))
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.dir_list.addItem(item)
            if str(path) in selected_paths:
                item.setSelected(True)
        self.dir_list.verticalScrollBar().setValue(scroll_pos)

    def _show_error(self, title: str, message: str):
        """Show standardized error dialog"""
        QMessageBox.critical(self, title, message)
        logger.error(f"{title}: {message}")

    def closeEvent(self, event):
        """Handle dialog closing - hide instead of close during import."""
        if self.import_worker and self.import_worker.isRunning():
            # Import is in progress, hide the dialog instead of closing
            self._close_requested = True
            self.hide()

            # Update status to show import continues
            StatusManager.show_message(
                "Import continues in background. Reopen import dialog to monitor progress.",
                0,
            )

            # IMPORTANT: DO NOT disconnect signals - they must remain connected
            # for progress updates to continue working
            logger.info("Import in progress - dialog hidden instead of closed")
            event.ignore()  # Don't close, just hide
        else:
            # No import running, close normally
            self._close_requested = False
            event.accept()

    def showEvent(self, event):
        """Reset close request flag when dialog is shown again."""
        super().showEvent(event)
        self._close_requested = False
        # Update status note based on import state
        if self.import_worker and self.import_worker.isRunning():
            self.status_note.show()
        else:
            self.status_note.hide()

    def reject(self):
        """Handle ESC key or window close button."""
        self.close()

    def accept(self):
        """Handle dialog acceptance."""
        # If import is running, hide instead of accept
        if self.import_worker and self.import_worker.isRunning():
            self.close()
        else:
            super().accept()
