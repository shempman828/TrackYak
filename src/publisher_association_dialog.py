from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from src.logger_config import logger


class PublisherAssociationDialog(QDialog):
    """Dialog showing detailed publisher associations with albums and tracks."""

    def __init__(self, controller, publisher, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.publisher = publisher
        self.init_ui()
        self.load_associations()

    def init_ui(self):
        """Initialize association dialog UI."""
        self.setWindowTitle(f"Associations - {self.publisher.publisher_name}")
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout(self)

        # Albums list with tracks
        self.albums_tree = QTreeWidget()
        self.albums_tree.setHeaderLabels(["Album", "Year", "Tracks"])
        self.albums_tree.setSortingEnabled(True)
        layout.addWidget(self.albums_tree)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def load_associations(self):
        """Load album and track associations."""
        self.albums_tree.clear()

        try:
            album_links = self.controller.get.get_entity_links(
                "AlbumPublisher", publisher_id=self.publisher.publisher_id
            )

            for link in album_links:
                album = self.controller.get.get_entity_object(
                    "Album", album_id=link.album_id
                )
                if album:
                    album_item = QTreeWidgetItem(
                        [
                            album.album_name,
                            str(album.release_year)
                            if album.release_year
                            else "Unknown",
                            str(album.track_count or 0),
                        ]
                    )
                    album_item.setData(0, Qt.UserRole, album.album_id)

                    # Load tracks for this album using direct relationship
                    # Tracks have a foreign key to album_id
                    album_tracks = self.controller.get.get_all_entities(
                        "Track", album_id=album.album_id
                    )

                    for track in album_tracks:
                        track_item = QTreeWidgetItem(
                            [
                                track.track_name,
                                str(track.track_number) if track.track_number else "",
                                f"{track.duration // 60}:{track.duration % 60:02d}"
                                if track.duration
                                else "",
                            ]
                        )
                        album_item.addChild(track_item)

                    self.albums_tree.addTopLevelItem(album_item)

            self.albums_tree.expandAll()
            self.albums_tree.resizeColumnToContents(0)

        except Exception as e:
            logger.error(f"Error loading associations: {str(e)}")
