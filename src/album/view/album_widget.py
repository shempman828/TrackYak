"""Album thumbnail tile and the responsive, scrollable album grid built from it."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.common.widgets.style_utils import set_style_property
from src.foundation.censor import censor_text
from src.foundation.logger_config import logger
from src.image.artwork_cache import get_artwork_cache

ART_CORNER_RADIUS = 10


def rounded_pixmap(pixmap: QPixmap, radius: int = ART_CORNER_RADIUS) -> QPixmap:
    """Return a copy of pixmap with its corners clipped to a rounded rect."""
    if pixmap.isNull():
        return pixmap
    ratio = pixmap.devicePixelRatio()
    w, h = pixmap.width() / ratio, pixmap.height() / ratio

    rounded = QPixmap(pixmap.size())
    rounded.setDevicePixelRatio(ratio)
    rounded.fill(Qt.transparent)

    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded


class AlbumWidget(QWidget):
    """Individual widget representing a single album."""

    clicked = Signal(object)
    doubleClicked = Signal(object)

    _placeholder_cache: QPixmap | None = None

    def __init__(self, album, size=200, parent=None):
        super().__init__(parent)
        # Plain QWidget subclasses don't paint a QSS background/border unless
        # this is set -- needed for the [selected="true"]/[hovered="true"]
        # rules in dark_mode.qss to actually render.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.album = album
        self.art_size = size
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.setInterval(250)
        self.click_timer.timeout.connect(lambda: self.clicked.emit(self.album))

        # UI Components
        self.art_label = QLabel()
        self.title_label = QLabel()
        self.subtitle_label = QLabel()
        self.artist_label = QLabel()

        self.init_ui()
        self.refresh_display()

    def init_ui(self):
        """Lay out the art, title, subtitle and artist labels."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        self.art_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.art_label)

        self.title_label.setObjectName("AlbumTitleLabel")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.subtitle_label.setObjectName("AlbumSubtitleLabel")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setProperty("textRole", "note")
        layout.addWidget(self.subtitle_label)

        self.artist_label.setObjectName("AlbumArtistLabel")
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setWordWrap(True)
        layout.addWidget(self.artist_label)

        # Fixed size so the tile does not stretch to fill a grid cell.
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def refresh_display(self):
        """Update the art, labels, tooltip and size from the current album data."""
        pixmap = self._load_art()
        scaled_pixmap = pixmap.scaled(self.art_size, self.art_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.art_label.setPixmap(rounded_pixmap(scaled_pixmap))

        album_name = censor_text(str(getattr(self.album, "album_name", None) or "Unknown Album"))
        release_year = getattr(self.album, "release_year", "")
        year_str = f" ({release_year})" if release_year else ""

        mb_badge = " \U0001f517" if getattr(self.album, "MBID", None) else ""
        self.title_label.setText(f"{album_name}{year_str}{mb_badge}")

        subtitle = getattr(self.album, "album_subtitle", None)
        subtitle = censor_text(str(subtitle)) if subtitle else None
        if subtitle:
            self.subtitle_label.setText(f"({subtitle})")
            self.subtitle_label.show()
        else:
            self.subtitle_label.clear()
            self.subtitle_label.hide()

        artist_text = getattr(self.album, "album_artist_names", None) or "Unknown Artist"
        self.artist_label.setText(artist_text)

        subtitle_line = f"\n({subtitle})" if subtitle else ""
        mb_line = "\nLinked to MusicBrainz" if getattr(self.album, "MBID", None) else ""
        self.setToolTip(f"{album_name}{year_str}{subtitle_line}\n{artist_text}{mb_line}")
        extra_height = 20 if subtitle else 0
        self.setFixedSize(self.art_size + 20, self.art_size + 90 + extra_height)

    def _load_art(self):
        """Return the cached front cover, or the shared placeholder when there is none."""
        is_explicit = bool(getattr(self.album, "art_is_explicit", False))
        cache = get_artwork_cache()
        pixmap = cache.get_pixmap(self.album, "front", is_explicit) if cache else None
        if pixmap and not pixmap.isNull():
            return pixmap
        if AlbumWidget._placeholder_cache is None:
            AlbumWidget._placeholder_cache = self._create_placeholder()
        return AlbumWidget._placeholder_cache

    def _create_placeholder(self):
        """Paint a rounded gradient tile with a music-note glyph for albums without art."""
        # Same "no art" look as nowplaying_art.py::_paint_placeholder_in_rect.
        canvas_size = 256
        pixmap = QPixmap(canvas_size, canvas_size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, canvas_size, canvas_size, ART_CORNER_RADIUS, ART_CORNER_RADIUS)
        painter.setClipPath(path)

        gradient = QLinearGradient(0, 0, canvas_size, canvas_size)
        gradient.setColorAt(0.0, QColor("#1a1b26"))
        gradient.setColorAt(1.0, QColor("#0e0f15"))
        painter.fillPath(path, gradient)

        painter.setClipping(False)
        note_font = QFont(self.font().family(), canvas_size // 5, QFont.Bold)
        painter.setFont(note_font)
        painter.setPen(QColor("#333a52"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "♪")
        painter.end()
        return pixmap

    def update_size(self, new_size):
        """Resize the tile to a new art size."""
        self.art_size = new_size
        self.refresh_display()

    def refresh_album(self, album):
        """Update this widget in place to reflect fresh album data (e.g. after editing)."""
        logger.debug(f"Refreshing album widget in place: {getattr(album, 'album_name', '?')}")
        self.album = album
        self.refresh_display()

    def mousePressEvent(self, event):
        """Start the single-click timer; a double-click cancels it."""
        if event.button() == Qt.LeftButton:
            self.click_timer.start()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Emit doubleClicked once and suppress the pending single click."""
        # No super() call: QWidget's default re-dispatches to mousePressEvent,
        # which would restart the single-click timer.
        if event.button() == Qt.LeftButton:
            self.click_timer.stop()
            self.doubleClicked.emit(self.album)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        """Show the hover style."""
        set_style_property(self, "hovered", True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Clear the hover style."""
        set_style_property(self, "hovered", False)
        super().leaveEvent(event)


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
        self.current_columns = None

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setSpacing(15)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.refresh_grid()

    def set_albums(self, albums):
        """Replace the album list and rebuild the grid."""
        self.albums = albums
        self.refresh_grid()

    def calculate_columns(self):
        """Return the fixed column count, or how many tiles fit the current width."""
        if self.columns:
            return self.columns
        width = self.width() or 800
        return max(1, width // (self.album_size + 40))

    def refresh_grid(self):
        """Recreate every AlbumWidget; use only when the album list changes (resize uses relayout)."""
        # Hide before deleteLater() so a stale tile never overlaps its replacement.
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.widgets.clear()

        if not self.albums:
            self.current_columns = None
            return

        cols = self.calculate_columns()
        self.current_columns = cols
        for i, album in enumerate(self.albums):
            w = AlbumWidget(album, self.album_size)
            w.clicked.connect(self.albumClicked.emit)
            w.doubleClicked.connect(self.albumDoubleClicked.emit)

            self._grid.addWidget(w, i // cols, i % cols)
            self.widgets.append(w)

    def relayout(self):
        """Move existing tiles into a new column count without recreating them."""
        if not self.widgets:
            return

        cols = self.calculate_columns()
        if cols == self.current_columns:
            return

        self.current_columns = cols
        for i, w in enumerate(self.widgets):
            self._grid.addWidget(w, i // cols, i % cols)

    def resizeEvent(self, event):
        """Reflow the tiles when the width changes and no column count is fixed."""
        super().resizeEvent(event)
        if not self.columns:
            self.relayout()


class ScrollableAlbumFlow(QScrollArea):
    """Scrollable, resizable grid of AlbumWidgets -- the ready-to-embed album-grid component."""

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
        """Replace the album list shown in the grid."""
        self.flow.set_albums(albums)
