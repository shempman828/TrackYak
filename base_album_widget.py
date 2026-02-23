from pathlib import Path
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
)


class AlbumWidget(QWidget):
    """
    Individual widget representing a single album.
    """

    clicked = Signal(object)
    doubleClicked = Signal(object)

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        self.album = album
        self.size = size
        self.is_selected = False
        self.click_timer = None

        # UI Components
        self.art_label = QLabel()
        self.title_label = QLabel()
        self.artist_label = QLabel()

        self.init_ui()
        self.refresh_display()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        self.art_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.art_label)

        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 1.1em;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setWordWrap(True)
        self.artist_label.setStyleSheet("color: #555;")
        layout.addWidget(self.artist_label)

        # Ensure the widget doesn't expand weirdly in a grid
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def refresh_display(self):
        """Updates all visual elements based on the current album data."""
        # 1. Load Art
        pixmap = self._load_art()
        scaled_pixmap = pixmap.scaled(
            self.size, self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.art_label.setPixmap(scaled_pixmap)

        # 2. Set Text
        album_name = str(getattr(self.album, "album_name", "Unknown Album"))
        release_year = getattr(self.album, "release_year", "")
        year_str = f" ({release_year})" if release_year else ""

        self.title_label.setText(f"{album_name}{year_str}")

        # 3. Handle Artists (Unified logic)
        artists = []
        for attr in ["album_artist_names", "artist_names", "album_artists"]:
            val = getattr(self.album, attr, [])
            if val:
                artists = [
                    a.artist_name if hasattr(a, "artist_name") else str(a) for a in val
                ]
                break

        artist_text = ", ".join(artists) if artists else "Unknown Artist"
        self.artist_label.setText(artist_text)

        # 4. Tooltip & Size
        self.setToolTip(f"{album_name}{year_str}\n{artist_text}")
        self.setFixedSize(self.size + 20, self.size + 90)

    def _load_art(self):
        path = getattr(self.album, "front_cover_path", None)
        if path and Path(path).exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
        return self._create_placeholder()

    def _create_placeholder(self):
        canvas_size = 256
        pixmap = QPixmap(canvas_size, canvas_size)
        pixmap.fill(QColor(240, 240, 240))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(180, 180, 180))
        painter.setFont(QFont("Arial", 12))

        rect = QFontMetrics(painter.font()).boundingRect("No Art")
        painter.drawText(
            (canvas_size - rect.width()) // 2,
            (canvas_size + rect.height()) // 2,
            "No Art",
        )
        painter.end()
        return pixmap

    def set_selected(self, selected):
        self.is_selected = selected
        if selected:
            self.setStyleSheet(
                "border: 2px solid #0078d7; background-color: #f0f8ff; border-radius: 4px;"
            )
        else:
            self.setStyleSheet("")

    def update_size(self, new_size):
        self.size = new_size
        self.refresh_display()

    # --- Mouse Events ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.click_timer and self.click_timer.isActive():
                self.click_timer.stop()
                self.doubleClicked.emit(self.album)
            else:
                self.click_timer = QTimer.singleShot(
                    250, lambda: self.clicked.emit(self.album)
                )
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.album)
        super().mouseDoubleClickEvent(event)


class AlbumFlowWidget(QWidget):
    """Responsive grid container for AlbumWidgets."""

    albumClicked = Signal(object)
    albumDoubleClicked = Signal(object)

    def __init__(self, albums=None, album_size=200, columns=None, parent=None):
        super().__init__(parent)
        self.albums = albums or []
        self.album_size = album_size
        self.columns = columns
        self.widgets = []

        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(15)
        self.layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.refresh_grid()

    def set_albums(self, albums):
        self.albums = albums
        self.refresh_grid()

    def calculate_columns(self):
        if self.columns:
            return self.columns
        width = self.width() or 800
        return max(1, width // (self.album_size + 40))

    def refresh_grid(self):
        # Clear existing
        for w in self.widgets:
            w.deleteLater()
        self.widgets.clear()

        if not self.albums:
            return

        cols = self.calculate_columns()
        for i, album in enumerate(self.albums):
            w = AlbumWidget(album, self.album_size)
            w.clicked.connect(self.albumClicked.emit)
            w.doubleClicked.connect(self.albumDoubleClicked.emit)

            self.layout.addWidget(w, i // cols, i % cols)
            self.widgets.append(w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Simple debounce-free refresh for smoother window snapping
        # If performance drops with 1000+ albums, re-add the QTimer debounce
        if not self.columns:
            self.refresh_grid()


class ScrollableAlbumFlow(QScrollArea):
    """The complete 'Best Version' component for use in UIs."""

    def __init__(self, albums=None, album_size=200, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)

        self.flow = AlbumFlowWidget(albums, album_size)
        self.setWidget(self.flow)

        # Proxy signals
        self.albumClicked = self.flow.albumClicked
        self.albumDoubleClicked = self.flow.albumDoubleClicked

    def set_albums(self, albums):
        self.flow.set_albums(albums)
