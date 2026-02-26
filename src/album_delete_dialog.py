from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class DeleteEmptyAlbumsDialog(QDialog):
    """Dialog to confirm deletion of empty albums."""

    def __init__(self, empty_albums, parent=None):
        super().__init__(parent)
        self.empty_albums = empty_albums
        self.setWindowTitle("Delete Empty Albums - Confirmation")
        self.setMinimumSize(500, 400)
        self.init_ui()

    def init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout(self)

        # Information label
        info_label = QLabel(
            f"The following {len(self.empty_albums)} album(s) have no tracks and will be deleted:"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Scroll area with album list
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        # Add each album to the list
        for album in self.empty_albums:
            album_name = getattr(album, "album_name", "Unknown Album")
            release_year = getattr(album, "release_year", "Unknown Year")

            # Get artist names
            artist_names = []
            for artist in getattr(album, "album_artists", []):
                if hasattr(artist, "artist_name"):
                    artist_names.append(artist.artist_name)
                elif isinstance(artist, str):
                    artist_names.append(artist)

            artist_text = ", ".join(artist_names) if artist_names else "Unknown Artist"

            # Create album entry
            album_widget = QWidget()
            album_layout = QHBoxLayout(album_widget)
            album_layout.setContentsMargins(5, 2, 5, 2)

            # Album info
            info_text = f"<b>{album_name}</b> ({release_year}) - {artist_text}"
            info_label = QLabel(info_text)
            album_layout.addWidget(info_label)
            album_layout.addStretch()

            scroll_layout.addWidget(album_widget)

        scroll_content.setLayout(scroll_layout)
        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        # Warning label
        warning_label = QLabel(
            "<font color='red'><b>Warning:</b> This action cannot be undone. "
            "Empty albums will be permanently deleted from your library.</font>"
        )
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
