from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QListWidget,
    QPushButton,
    QHBoxLayout,
)


class PlaylistSelectionDialog(QDialog):
    """Dialog for selecting a playlist."""

    def __init__(self, playlists, controller, parent=None):
        super().__init__(parent)
        self.playlists = playlists
        self.controller = controller
        self.selected_playlist = None

        self.setWindowTitle("Select Playlist")
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)

        # Playlist list
        self.playlist_list = QListWidget()
        self.playlist_list.itemDoubleClicked.connect(self.accept_selection)

        # Populate list
        for playlist in self.playlists:
            self.playlist_list.addItem(
                f"{playlist.playlist_name} ({playlist.track_count} tracks)"
            )

        layout.addWidget(self.playlist_list)

        # Buttons
        button_layout = QHBoxLayout()

        self.select_button = QPushButton("Select")
        self.select_button.clicked.connect(self.accept_selection)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    def accept_selection(self):
        """Accept the current selection."""
        current_row = self.playlist_list.currentRow()
        if 0 <= current_row < len(self.playlists):
            self.selected_playlist = self.playlists[current_row]
            self.accept()

    def get_selected_playlist(self):
        """Return the selected playlist."""
        return self.selected_playlist
