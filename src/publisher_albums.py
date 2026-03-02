from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.base_album_widget import AlbumFlowWidget
from src.logger_config import logger


class PublisherAlbumsWindow(QWidget):
    """Standalone window that loads and displays a publisher's albums on demand."""

    def __init__(self, controller, publisher, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.publisher = publisher
        self.setWindowTitle(f"Albums — {publisher.publisher_name}")
        self.resize(1000, 700)
        self._init_ui()
        self._load_albums()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.status_label = QLabel("Loading albums...")
        layout.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.flow = AlbumFlowWidget(albums=[], album_size=160)
        scroll.setWidget(self.flow)
        layout.addWidget(scroll)

    def _load_albums(self):
        try:
            album_links = self.controller.get.get_entity_links(
                "AlbumPublisher", publisher_id=self.publisher.publisher_id
            )
            albums = []
            for link in album_links:
                album = self.controller.get.get_entity_object(
                    "Album", album_id=link.album_id
                )
                if album:
                    albums.append(album)

            self.flow.set_albums(albums)
            self.status_label.setText(
                f"{len(albums)} album(s) — {self.publisher.publisher_name}"
            )
        except Exception as e:
            logger.error(f"Error loading albums window: {str(e)}")
            self.status_label.setText("Error loading albums.")
