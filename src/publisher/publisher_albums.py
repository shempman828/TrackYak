from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.album.edit.base_album_edit import AlbumEditor
from src.album.edit.base_album_widget import ScrollableAlbumFlow
from src.foundation.logger_config import logger
from src.publisher.publisher_hierarchy import get_publisher_albums


class PublisherAlbumsWindow(QDialog):
    """Popup dialog that loads and displays a publisher's albums on demand."""

    # Emitted after an album is edited from this window, so the owning
    # publisher detail panel and tree (whose album counts are otherwise
    # left stale) can refresh themselves.
    albums_changed = Signal()

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
        self.flow.albumDoubleClicked.connect(self._open_album_editor)
        layout.addWidget(self.flow)

    def _open_album_editor(self, album):
        try:
            fresh = self.controller.get.get_entity_object("Album", album_id=album.album_id)
            if fresh:
                album = fresh
        except SQLAlchemyError as e:
            logger.error(f"Error refreshing album {album.album_id} before editing: {e!s}")
            return

        dialog = AlbumEditor(self.controller, album, self)
        dialog.exec()
        self._load_albums()
        self.albums_changed.emit()

    def _load_albums(self):
        try:
            albums = get_publisher_albums(self.controller, self.publisher.publisher_id)

            self.flow.set_albums(albums)
            count = len(albums)
            self.status_label.setText(f"{count} album{'s' if count != 1 else ''} — {self.publisher.publisher_name}")
        except SQLAlchemyError as e:
            logger.error(f"Error loading albums window: {e!s}")
            self.status_label.setText("Error loading albums.")
