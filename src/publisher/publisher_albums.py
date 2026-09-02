from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.album.base_album_widget import ScrollableAlbumFlow
from src.foundation.logger_config import logger
from src.publisher.publisher_hierarchy import get_publisher_albums


class PublisherAlbumsWindow(QDialog):
    """Popup dialog that loads and displays a publisher's albums on demand."""

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

        # ScrollableAlbumFlow handles the scroll area + grid layout correctly
        self.flow = ScrollableAlbumFlow(albums=[], album_size=160)
        layout.addWidget(self.flow)

    def _load_albums(self):
        try:
            albums = get_publisher_albums(self.controller, self.publisher.publisher_id)

            self.flow.set_albums(albums)
            count = len(albums)
            self.status_label.setText(
                f"{count} album{'s' if count != 1 else ''} — {self.publisher.publisher_name}"
            )
        except SQLAlchemyError as e:
            logger.error(f"Error loading albums window: {e!s}")
            self.status_label.setText("Error loading albums.")
