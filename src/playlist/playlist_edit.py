from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class EditPlaylist(QDialog):
    """Dialog for editing playlist name and description (normal playlists only)."""

    def __init__(self, controller, playlist):
        """
        Initialize the edit dialog.

        :param controller: The controller for database operations
        :param playlist: The playlist object to edit
        """
        super().__init__()
        self.controller = controller
        self.playlist = playlist
        self.playlist_id = playlist.playlist_id

        # Check if this is a smart playlist
        self.is_smart_playlist = getattr(playlist, "is_smart", False)

        self.init_ui()
        self.setWindowTitle(f"Edit Playlist: {playlist.playlist_name}")

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout(self)

        # Name field
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit(self.playlist.playlist_name)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Description field
        desc_layout = QVBoxLayout()
        desc_layout.addWidget(QLabel("Description:"))
        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(100)
        self.desc_edit.setPlainText(getattr(self.playlist, "description", "") or "")
        desc_layout.addWidget(self.desc_edit)
        layout.addLayout(desc_layout)

        # Warning label for smart playlists
        if self.is_smart_playlist:
            warning_label = QLabel(
                "⚠️ Smart playlists can only be edited in Smart Playlist Editor"
            )
            warning_label.setProperty("danger", True)
            layout.addWidget(warning_label)
            self.name_edit.setEnabled(False)
            self.desc_edit.setEnabled(False)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_changes)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setMinimumWidth(400)

    def save_changes(self):
        """Save the changes to the playlist."""
        if self.is_smart_playlist:
            logger.debug(
                f"Edit blocked for smart playlist {self.playlist_id}; "
                "use Smart Playlist Editor instead"
            )
            show_status_message(
                self,
                "Smart playlists cannot be edited here. Use the Smart Playlist Editor instead.",
            )
            return

        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText().strip()

        if not name:
            logger.debug(
                f"Playlist edit rejected for playlist {self.playlist_id}: name is empty"
            )
            QMessageBox.warning(
                self, "Validation Error", "Playlist name cannot be empty."
            )
            self.name_edit.setFocus()
            return

        try:
            # Update playlist in database
            self.controller.update.update_entity(
                "Playlist",
                self.playlist_id,
                playlist_name=name,
                playlist_description=description,
            )

            logger.info(f"Updated playlist {self.playlist_id}: {name}")
            self.accept()

        except SQLAlchemyError as e:
            logger.error(f"Failed to update playlist {self.playlist_id}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update playlist: {str(e)}")
