"""
NowPlayingView module — Cinematic redesign.

A full-bleed, immersive "now playing" experience:
  - Album art dominates the left half as a large, softly-shadowed card
  - A blurred, colour-extracted backdrop bleeds behind everything
  - Track title / artist / album in refined, layered typography
  - Pill-shaped metadata chips (BPM, Key, Duration, Bitrate…)
  - Scrollable lyrics panel with fade-in/out at the edges
  - Smooth cross-fade when the track changes via QPropertyAnimation
"""

import traceback
from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from asset_paths import asset
from logger_config import logger

# ──────────────────────────────────────────────────────────────────────────────
#  Helper widgets
# ──────────────────────────────────────────────────────────────────────────────


class _BlurredBackdrop(QWidget):
    """
    Paints a heavily blurred + darkened version of the album art
    as a full-bleed background.  Opacity is animated on track change.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pixmap: QPixmap | None = None
        self._opacity: float = 0.0

    # Qt property so QPropertyAnimation can drive it
    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = v
        self.update()

    backdropOpacity = Property(float, _get_opacity, _set_opacity)

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # Deep background
        painter.fillRect(0, 0, w, h, QColor("#080a0f"))

        if self._pixmap and not self._pixmap.isNull() and self._opacity > 0:
            # Scale to fill
            scaled = self._pixmap.scaled(
                w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2

            painter.setOpacity(self._opacity * 0.38)
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)

        # Gradient vignette — darkens edges and bottom
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(8, 10, 15, 200))
        grad.setColorAt(0.45, QColor(8, 10, 15, 80))
        grad.setColorAt(1.0, QColor(8, 10, 15, 240))
        painter.fillRect(0, 0, w, h, grad)

        painter.end()


class _ArtCard(QLabel):
    """Rounded album-art card with a drop shadow painted underneath."""

    RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._art: QPixmap | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_art(self, pixmap: QPixmap | None):
        self._art = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        r = self.RADIUS

        if self._art and not self._art.isNull():
            side = min(w, h) - 24  # 12px margin each side
            x = (w - side) // 2
            y = (h - side) // 2

            # Soft shadow (painted as a filled rounded rect offset below)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(x + 4, y + 8, side, side, r, r)
            painter.fillPath(shadow_path, QColor(0, 0, 0, 120))

            # Clip art to rounded rect
            clip = QPainterPath()
            clip.addRoundedRect(x, y, side, side, r, r)
            painter.setClipPath(clip)

            scaled = self._art.scaled(
                side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            sx = (side - scaled.width()) // 2
            sy = (side - scaled.height()) // 2
            painter.drawPixmap(x + sx, y + sy, scaled)

            # Thin border
            painter.setClipping(False)
            pen = QPen(QColor(255, 255, 255, 28))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(x, y, side, side, r, r)
        else:
            # Placeholder
            side = min(w, h) - 24
            x = (w - side) // 2
            y = (h - side) // 2
            clip = QPainterPath()
            clip.addRoundedRect(x, y, side, side, r, r)
            painter.setClipPath(clip)
            grad = QRadialGradient(w / 2, h / 2, side * 0.6)
            grad.setColorAt(0, QColor("#1a1d2e"))
            grad.setColorAt(1, QColor("#0d0f18"))
            painter.fillRect(x, y, side, side, grad)
            painter.setClipping(False)
            painter.setPen(QColor(133, 153, 234, 60))
            painter.setFont(QFont("Segoe UI Symbol", int(side * 0.25)))
            painter.drawText(x, y, side, side, Qt.AlignCenter, "♪")

        painter.end()


class _Chip(QFrame):
    """A small pill-shaped metadata chip: icon + value label."""

    def __init__(self, icon_text: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setObjectName("MetadataChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            QFrame#MetadataChip {
                background: rgba(133, 153, 234, 0.12);
                border: 1px solid rgba(133, 153, 234, 0.28);
                border-radius: 10px;
            }
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(5)

        self._icon_lbl = QLabel(icon_text)
        self._icon_lbl.setStyleSheet(
            "color: #8599ea; font-size: 11px; background: transparent; border: none;"
        )
        self._icon_lbl.setAlignment(Qt.AlignCenter)

        self._val_lbl = QLabel(value)
        self._val_lbl.setStyleSheet(
            "color: #c8d0f4; font-size: 11px; font-weight: 600; background: transparent; border: none;"
        )

        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._val_lbl)

    def set_value(self, value: str):
        self._val_lbl.setText(value)

    def set_visible_if(self, condition: bool):
        self.setVisible(condition)


class _FadedScrollArea(QScrollArea):
    """Scroll area that paints top/bottom fade gradients over the content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self.viewport())
        h = self.viewport().height()
        w = self.viewport().width()
        fade = 32

        top = QLinearGradient(0, 0, 0, fade)
        top.setColorAt(0, QColor(8, 10, 15, 200))
        top.setColorAt(1, QColor(8, 10, 15, 0))
        p.fillRect(0, 0, w, fade, top)

        bot = QLinearGradient(0, h - fade, 0, h)
        bot.setColorAt(0, QColor(8, 10, 15, 0))
        bot.setColorAt(1, QColor(8, 10, 15, 200))
        p.fillRect(0, h - fade, w, fade, bot)
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
#  Main view
# ──────────────────────────────────────────────────────────────────────────────


class NowPlayingView(QWidget):
    """Cinematic now-playing view with blurred backdrop and rich metadata."""

    # ── fonts ──────────────────────────────────────────────────────────────
    _TITLE_FONT = QFont("Georgia", 28, QFont.Bold)
    _ARTIST_FONT = QFont("Cambria", 16, QFont.Normal)
    _ALBUM_FONT = QFont("Cambria", 13, QFont.Normal)
    _LYRICS_FONT = QFont("Cambria", 12, QFont.Normal)
    _LABEL_FONT = QFont("Cambria", 11, QFont.Normal)

    def __init__(self, controller, track=None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self._current_pixmap: QPixmap | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._initUI()
        if self.track:
            self.updateUI(self.track)
        else:
            self.clearUI()

    # ── build UI ───────────────────────────────────────────────────────────

    def _initUI(self):
        self.setMinimumSize(760, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent;")

        # ── backdrop (parent-level, behind everything) ──
        self._backdrop = _BlurredBackdrop(self)
        self._backdrop.lower()

        # ── root horizontal layout ──
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT — album art column ──────────────────────────────────────
        left_widget = QWidget()
        left_widget.setStyleSheet("background: transparent;")
        left_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_widget.setMinimumWidth(260)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(32, 36, 16, 36)
        left_layout.setSpacing(0)
        left_layout.setAlignment(Qt.AlignCenter)

        self._art_card = _ArtCard()
        self._art_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout.addWidget(self._art_card)

        # ── RIGHT — info column ──────────────────────────────────────────
        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(16, 40, 40, 32)
        right_layout.setSpacing(0)
        right_layout.setAlignment(Qt.AlignTop)

        # Track title
        self._title_lbl = QLabel("No Track Playing")
        self._title_lbl.setFont(self._TITLE_FONT)
        self._title_lbl.setStyleSheet(
            "color: #e8ecff; background: transparent; border: none;"
        )
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_layout.addWidget(self._title_lbl)
        right_layout.addSpacing(6)

        # Artist
        self._artist_lbl = QLabel("—")
        self._artist_lbl.setFont(self._ARTIST_FONT)
        self._artist_lbl.setStyleSheet(
            "color: #8599ea; background: transparent; border: none;"
        )
        self._artist_lbl.setWordWrap(True)
        right_layout.addWidget(self._artist_lbl)
        right_layout.addSpacing(2)

        # Album
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setStyleSheet(
            "color: #6a7299; background: transparent; border: none;"
        )
        self._album_lbl.setWordWrap(True)
        right_layout.addWidget(self._album_lbl)
        right_layout.addSpacing(20)

        # ── Metadata chip row ──
        chip_widget = QWidget()
        chip_widget.setStyleSheet("background: transparent;")
        chip_row = QHBoxLayout(chip_widget)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(8)
        chip_row.setAlignment(Qt.AlignLeft)

        self._chip_duration = _Chip("⏱", "—")
        self._chip_bpm = _Chip("♩", "—")
        self._chip_key = _Chip("𝄞", "—")
        self._chip_bitrate = _Chip("≋", "—")
        self._chip_track_no = _Chip("#", "—")

        for chip in (
            self._chip_duration,
            self._chip_bpm,
            self._chip_key,
            self._chip_bitrate,
            self._chip_track_no,
        ):
            chip_row.addWidget(chip)

        chip_row.addStretch()
        right_layout.addWidget(chip_widget)
        right_layout.addSpacing(20)

        # ── Lyrics section ──────────────────────────────────────────────
        lyrics_header_row = QHBoxLayout()
        lyrics_header_row.setContentsMargins(0, 0, 0, 4)

        self._lyrics_section_lbl = QLabel("LYRICS")
        self._lyrics_section_lbl.setFont(QFont("Cambria", 10, QFont.Bold))
        self._lyrics_section_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.55); letter-spacing: 2px; background: transparent; border: none;"
        )
        lyrics_header_row.addWidget(self._lyrics_section_lbl)
        lyrics_header_row.addStretch()

        right_layout.addLayout(lyrics_header_row)

        # Thin divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(
            "border: none; border-top: 1px solid rgba(133,153,234,0.18); background: transparent;"
        )
        divider.setFixedHeight(1)
        right_layout.addWidget(divider)
        right_layout.addSpacing(10)

        # Scrollable lyrics
        self._lyrics_area = _FadedScrollArea()
        self._lyrics_area.setStyleSheet("background: transparent; border: none;")
        self._lyrics_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._lyrics_lbl = QLabel()
        self._lyrics_lbl.setFont(self._LYRICS_FONT)
        self._lyrics_lbl.setStyleSheet(
            "color: rgba(200, 208, 244, 0.75); line-height: 1.7em; background: transparent; border: none;"
        )
        self._lyrics_lbl.setWordWrap(True)
        self._lyrics_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._lyrics_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._lyrics_lbl.setContentsMargins(0, 8, 0, 24)
        self._lyrics_area.setWidget(self._lyrics_lbl)

        right_layout.addWidget(self._lyrics_area)

        # ── assemble root ──
        root.addWidget(left_widget, 42)
        root.addWidget(right_widget, 58)

    # ── resize: keep backdrop full size ───────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._backdrop.setGeometry(0, 0, self.width(), self.height())

    # ── public API ────────────────────────────────────────────────────────

    def updateUI(self, track):
        """Populate all widgets from a track ORM object."""
        try:
            if not track:
                self.clearUI()
                return

            logger.info(f"NowPlayingView.updateUI: {getattr(track, 'track_name', '?')}")

            # ── title
            title = getattr(track, "track_name", None) or "Unknown Title"
            self._title_lbl.setText(title)

            # ── artist
            artists = getattr(track, "artists", None) or []
            artist_name = ""
            if artists:
                artist_name = getattr(artists[0], "artist_name", "") or ""
            self._artist_lbl.setText(artist_name or "—")

            # ── album
            album = getattr(track, "album", None)
            album_name = ""
            if album:
                album_name = getattr(album, "album_name", "") or ""
            self._album_lbl.setText(album_name or "—")

            # ── chips
            self._update_chips(track)

            # ── lyrics
            lyrics = getattr(track, "lyrics", None)
            if lyrics and lyrics.strip():
                self._lyrics_lbl.setText(lyrics)
                self._lyrics_section_lbl.setVisible(True)
                self._lyrics_area.setVisible(True)
            else:
                self._lyrics_lbl.setText("No lyrics available.")
                self._lyrics_section_lbl.setVisible(True)
                self._lyrics_area.setVisible(True)

            # ── album art
            art_path: Path | None = None
            if album:
                art_str = getattr(album, "front_cover_path", "") or ""
                if art_str:
                    art_path = Path(art_str)

            if art_path and art_path.exists():
                self._load_art(QPixmap(str(art_path)))
            elif self.default_art_path and Path(self.default_art_path).exists():
                self._load_art(QPixmap(self.default_art_path))
            else:
                self._load_art(None)

        except Exception as e:
            logger.error(
                f"NowPlayingView.updateUI failed: {e}\n{traceback.format_exc()}"
            )
            self.clearUI()

    def clearUI(self):
        """Reset to the empty / idle state."""
        self._title_lbl.setText("No Track Playing")
        self._artist_lbl.setText("—")
        self._album_lbl.setText("—")
        self._lyrics_lbl.setText("")
        for chip in (
            self._chip_duration,
            self._chip_bpm,
            self._chip_key,
            self._chip_bitrate,
            self._chip_track_no,
        ):
            chip.set_value("—")
            chip.setVisible(False)
        if self.default_art_path and Path(self.default_art_path).exists():
            self._load_art(QPixmap(self.default_art_path))
        else:
            self._load_art(None)

    # ── internal helpers ──────────────────────────────────────────────────

    def _update_chips(self, track):
        """Fill metadata chips; hide any that have no data."""
        # Duration
        dur = getattr(track, "duration", None)
        if dur:
            mins, secs = int(dur) // 60, int(dur) % 60
            self._chip_duration.set_value(f"{mins}:{secs:02d}")
            self._chip_duration.setVisible(True)
        else:
            self._chip_duration.setVisible(False)

        # BPM
        bpm = getattr(track, "bpm", None)
        if bpm:
            self._chip_bpm.set_value(f"{float(bpm):.0f} BPM")
            self._chip_bpm.setVisible(True)
        else:
            self._chip_bpm.setVisible(False)

        # Key
        key = getattr(track, "key", None)
        if key:
            mode = getattr(track, "mode", "") or ""
            self._chip_key.set_value(f"{key} {mode}".strip())
            self._chip_key.setVisible(True)
        else:
            self._chip_key.setVisible(False)

        # Bitrate
        bitrate = getattr(track, "bit_rate", None)
        if bitrate:
            self._chip_bitrate.set_value(f"{int(bitrate)} kbps")
            self._chip_bitrate.setVisible(True)
        else:
            self._chip_bitrate.setVisible(False)

        # Track number
        track_no = getattr(track, "track_number", None)
        if track_no:
            self._chip_track_no.set_value(f"Track {track_no}")
            self._chip_track_no.setVisible(True)
        else:
            self._chip_track_no.setVisible(False)

    def _load_art(self, pixmap: QPixmap | None):
        """Update album art and animate the backdrop into view."""
        self._current_pixmap = pixmap
        self._art_card.set_art(pixmap)

        # Animate backdrop opacity 0 → 1
        if self._fade_anim:
            self._fade_anim.stop()

        self._backdrop.set_pixmap(pixmap)
        self._backdrop._opacity = 0.0

        self._fade_anim = QPropertyAnimation(self._backdrop, b"backdropOpacity")
        self._fade_anim.setDuration(600)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade_anim.start()
