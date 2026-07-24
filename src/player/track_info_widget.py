from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.core.censor import censor_text


class ScrollingLabel(QLabel):
    """
    A QLabel that smoothly scrolls its text from right to left when the text
    is too wide to fit inside the widget.  When the text fits, it just sits
    centred like a normal label.

    How it works:
    - A QTimer fires every ~40 ms (≈25 fps).
    - Each tick we nudge an internal pixel offset forward by `scroll_speed`.
    - paintEvent draws the text at that offset so it glides across.
    - Once the text has fully scrolled off the left edge we reset the offset
      back to the start and pause briefly before repeating.
    """

    def __init__(self, text="", scroll_speed=1, pause_ms=1500, parent=None):
        super().__init__(text, parent)
        self.scroll_speed = scroll_speed  # pixels per tick
        self._offset = 0  # current horizontal scroll position
        self._paused = False  # True while we're in the pause gap

        # The timer drives the animation
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25 fps, smooth enough
        self._timer.timeout.connect(self._tick)

        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

    # ── Public helpers ────────────────────────────────────────────────────────

    def setText(self, text):
        """Override so we restart the scroll whenever the text changes."""
        super().setText(text)
        self._reset()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _reset(self):
        self._offset = 0
        self._paused = False
        self._timer.stop()
        self.update()  # force a repaint with the new text
        self._maybe_start()  # only start the timer if scrolling is needed

    def _maybe_start(self):
        """Start the timer only when the text is actually wider than the widget."""
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        if text_w > self.width():
            # Short pause before the text starts moving so the user can read
            # the beginning first.
            QTimer.singleShot(1500, self._start_scroll)

    def _start_scroll(self):
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        if self._paused:
            return
        self._offset += self.scroll_speed
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        # Once the text has scrolled fully off the left edge, reset with a pause
        if self._offset > text_w + self.width() // 2:
            self._offset = 0
            self._paused = True
            QTimer.singleShot(1500, self._unpause)
        self.update()

    def _unpause(self):
        self._paused = False

    # ── Drawing ───────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-check whether scrolling is needed after the widget is resized
        self._reset()

    def paintEvent(self, event):
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())

        if text_w <= self.width():
            # Text fits — just draw it normally (centred)
            super().paintEvent(event)
            return

        # Text is too long — draw it at the current scroll offset
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        painter.drawText(-self._offset, y, self.text())
        painter.end()


class TrackInfoWidget(QWidget):
    """
    A small three-line display showing track name, artist, and album.

    - Track name  → text-primary color (#b8c0f0), scrolls if too long
    - Artist name → accent blue-purple (#8599ea), clickable → opens ArtistEditor
    - Album name  → gold (#EAD685), clickable → opens AlbumEditor
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._current_track = None

        layout = QVBoxLayout(self)
        layout.setSpacing(1)
        layout.setContentsMargins(0, 0, 0, 0)

        # Track title row — scrolling label
        self.title_label = ScrollingLabel("")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setObjectName("PlayerTrackLabel")
        self.title_label.setMinimumWidth(180)

        # Artist label — display only (editing via context menu on PlayerUI)
        self.artist_label = QLabel("")
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setObjectName("PlayerArtistLabel")

        # Album label — display only (editing via context menu on PlayerUI)
        self.album_label = QLabel("")
        self.album_label.setAlignment(Qt.AlignCenter)
        self.album_label.setObjectName("PlayerAlbumLabel")

        layout.addWidget(self.title_label)
        layout.addWidget(self.artist_label)
        layout.addWidget(self.album_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_track(self, track):
        """Populate the labels from a track ORM object."""
        self._current_track = track
        if track is None:
            self.clear()
            return

        title = getattr(track, "track_name", "") or "Unknown Title"
        self.title_label.setText(censor_text(title))

        # Artist
        artist_name = ""
        try:
            artist_name = getattr(track, "primary_artist_names", "") or ""
            if not artist_name:
                artists = getattr(track, "artists", []) or []
                if artists:
                    artist_name = getattr(artists[0], "artist_name", "") or ""
        except Exception:
            pass
        self.artist_label.setText(artist_name)
        # Hide the row entirely when there's nothing to show
        self.artist_label.setVisible(bool(artist_name))

        # Album (with release year in parentheses if available)
        album_name = getattr(track, "album_name", "") or ""
        release_year = getattr(track, "release_year", None)
        censored_album_name = censor_text(album_name)
        if album_name and release_year:
            album_display = f"{censored_album_name} ({release_year})"
        else:
            album_display = censored_album_name
        self.album_label.setText(album_display)
        self.album_label.setVisible(bool(album_name))

    def clear(self):
        self._current_track = None
        self.title_label.setText("")
        self.artist_label.setText("")
        self.album_label.setText("")
