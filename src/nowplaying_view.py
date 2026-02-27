"""
NowPlayingView module — Cinematic redesign.

Layout is always two columns (art | metadata+lyrics). The lyrics panel is
always present in the layout so the proportions never shift — it simply
shows nothing when a track has no lyrics.

Lyrics modes:
  - No lyrics        → lyrics area is blank; header/divider hidden
  - Plain text       → full scrollable text, no timestamps shown to user
  - Synced (LRC)     → karaoke style: only the CURRENT line is shown,
                       centred, large, fading in on each change.
                       Timestamps are never visible to the user.
"""

import re
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

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
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.asset_paths import asset
from src.logger_config import logger

# ──────────────────────────────────────────────────────────────────────────────
#  Lyrics parsing
# ──────────────────────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.,](\d+))?\](.*)$")


def _parse_lyrics(raw: str) -> Tuple[bool, List[Tuple[int, str]]]:
    """
    Parse raw lyrics string.

    Returns (is_synced, lines) where lines = [(start_ms, text), ...].
      is_synced=True  → LRC timestamps detected; lines sorted by time
      is_synced=False → plain text; every line gets timestamp 0
      Empty string    → (False, [])
    """
    if not raw or not raw.strip():
        return False, []

    parsed: List[Tuple[int, str]] = []
    for line in raw.splitlines():
        m = _TS_RE.match(line.strip())
        if m:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac = m.group(3) or "0"
            if len(frac) <= 2:
                ms_frac = int(frac) * (100 if len(frac) == 1 else 10)
            else:
                ms_frac = int(frac[:3])
            total_ms = (mins * 60 + secs) * 1000 + ms_frac
            parsed.append((total_ms, m.group(4).strip()))

    if parsed:
        parsed.sort(key=lambda x: x[0])
        return True, parsed

    plain = [(0, ln) for ln in raw.splitlines() if ln.strip()]
    return False, plain


def _active_index(lines: List[Tuple[int, str]], position_ms: int) -> int:
    """Index of the line current at position_ms."""
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts <= position_ms:
            idx = i
        else:
            break
    return idx


# ──────────────────────────────────────────────────────────────────────────────
#  Helper widgets
# ──────────────────────────────────────────────────────────────────────────────


class _BlurredBackdrop(QWidget):
    """Full-bleed blurred album-art backdrop, opacity-animated on track change."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._opacity: float = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_pixmap(self, pixmap: Optional[QPixmap]):
        self._pixmap = pixmap
        self.update()

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float):
        self._opacity = value
        self.update()

    backdropOpacity = Property(float, _get_opacity, _set_opacity)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setOpacity(self._opacity)
        w, h = self.width(), self.height()
        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            sx = (scaled.width() - w) // 2
            sy = (scaled.height() - h) // 2
            p.drawPixmap(0, 0, scaled, sx, sy, w, h)
            p.fillRect(0, 0, w, h, QColor(8, 10, 15, 210))
        else:
            grad = QRadialGradient(w * 0.5, h * 0.35, max(w, h) * 0.7)
            grad.setColorAt(0, QColor("#1a1d2e"))
            grad.setColorAt(1, QColor("#08090f"))
            p.fillRect(0, 0, w, h, grad)
        p.end()


class _ArtCard(QWidget):
    """Rounded album-art card with soft drop shadow."""

    RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._art: Optional[QPixmap] = None
        self._art_cache: Optional[QPixmap] = None
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_art(self, pixmap: Optional[QPixmap]):
        self._art = pixmap
        self._art_cache = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        r = self.RADIUS
        side = min(w, h) - 24
        x = (w - side) // 2
        y = (h - side) // 2

        if self._art and not self._art.isNull():
            shadow = QPainterPath()
            shadow.addRoundedRect(x + 4, y + 8, side, side, r, r)
            painter.fillPath(shadow, QColor(0, 0, 0, 120))

            clip = QPainterPath()
            clip.addRoundedRect(x, y, side, side, r, r)
            painter.setClipPath(clip)

            if self._art_cache is None or self._art_cache.width() != side:
                self._art_cache = self._art.scaled(
                    side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
            sx = (side - self._art_cache.width()) // 2
            sy = (side - self._art_cache.height()) // 2
            painter.drawPixmap(x + sx, y + sy, self._art_cache)

            painter.setClipping(False)
            pen = QPen(QColor(255, 255, 255, 28))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(x, y, side, side, r, r)
        else:
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
    """Small pill-shaped metadata chip."""

    def __init__(self, icon_text: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setObjectName("MetadataChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#MetadataChip {
                background: rgba(133, 153, 234, 0.12);
                border: 1px solid rgba(133, 153, 234, 0.28);
                border-radius: 10px;
            }
        """)
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
            "color: #c8d0f4; font-size: 11px; font-weight: 600;"
            " background: transparent; border: none;"
        )
        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._val_lbl)

    def set_value(self, value: str):
        self._val_lbl.setText(value)

    def set_visible_if(self, condition: bool):
        self.setVisible(condition)


class _FadedScrollArea(QScrollArea):
    """Scroll area with top/bottom fade gradients."""

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
        fade = 40

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
#  Karaoke line — single centred label with fade-in on text change
# ──────────────────────────────────────────────────────────────────────────────


class _KaraokeLine(QLabel):
    """
    Displays one lyric line at a time, centred in its space.
    Every time the line changes it fades in from transparent.
    """

    _FONT = QFont("Georgia", 20, QFont.Normal)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(self._FONT)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "color: rgba(230, 235, 255, 0.0); background: transparent; border: none;"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._anim: Optional[QPropertyAnimation] = None
        self._opacity: float = 0.0

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = v
        alpha = int(v * 255)
        self.setStyleSheet(
            f"color: rgba(230, 235, 255, {alpha});"
            " background: transparent; border: none;"
        )

    lineOpacity = Property(float, _get_opacity, _set_opacity)

    def show_line(self, text: str):
        """Swap text and animate opacity 0 → 1."""
        self.setText(text)
        if self._anim:
            self._anim.stop()
        self._set_opacity(0.0)
        self._anim = QPropertyAnimation(self, b"lineOpacity")
        self._anim.setDuration(350)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def clear_line(self):
        if self._anim:
            self._anim.stop()
        self.setText("")
        self._set_opacity(0.0)


# ──────────────────────────────────────────────────────────────────────────────
#  Main view
# ──────────────────────────────────────────────────────────────────────────────


class NowPlayingView(QWidget):
    """Cinematic now-playing view with blurred backdrop and rich metadata."""

    _TITLE_FONT = QFont("Georgia", 28, QFont.Bold)
    _ARTIST_FONT = QFont("Cambria", 16, QFont.Normal)
    _ALBUM_FONT = QFont("Cambria", 13, QFont.Normal)
    _PLAIN_FONT = QFont("Cambria", 12, QFont.Normal)

    def __init__(self, controller, track=None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self._current_pixmap: Optional[QPixmap] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        # Lyrics state
        self._is_synced = False
        self._lyrics_lines: List[Tuple[int, str]] = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._sync_offset_ms = -500  # default: shift display 500ms early

        self._initUI()

        # Connect directly — no changes needed in main_window.py
        try:
            self.controller.mediaplayer.position_changed.connect(
                self._on_position_changed
            )
        except Exception as e:
            logger.warning(f"NowPlayingView: could not connect position_changed: {e}")

        if self.track:
            self.updateUI(self.track)
        else:
            self.clearUI()

    # ── build UI ──────────────────────────────────────────────────────────

    def _initUI(self):
        self.setMinimumSize(760, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent;")

        self._backdrop = _BlurredBackdrop(self)
        self._backdrop.lower()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT — album art ─────────────────────────────────────────────
        left_widget = QWidget()
        left_widget.setStyleSheet("background: transparent;")
        left_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_widget.setMinimumWidth(260)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(32, 36, 16, 36)

        self._art_card = _ArtCard()
        self._art_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout.addWidget(self._art_card)

        # ── RIGHT — metadata + lyrics ────────────────────────────────────
        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(16, 36, 32, 36)
        right_layout.setSpacing(0)

        # Title
        self._title_lbl = QLabel("No Track Playing")
        self._title_lbl.setFont(self._TITLE_FONT)
        self._title_lbl.setStyleSheet(
            "color: rgba(230,235,255,0.96); background: transparent; border: none;"
        )
        self._title_lbl.setWordWrap(True)
        right_layout.addWidget(self._title_lbl)
        right_layout.addSpacing(6)

        # Artist
        self._artist_lbl = QLabel("—")
        self._artist_lbl.setFont(self._ARTIST_FONT)
        self._artist_lbl.setStyleSheet(
            "color: rgba(200,208,244,0.75); background: transparent; border: none;"
        )
        right_layout.addWidget(self._artist_lbl)
        right_layout.addSpacing(4)

        # Album
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setStyleSheet(
            "color: rgba(200,208,244,0.50); background: transparent; border: none;"
        )
        self._album_lbl.setWordWrap(True)
        right_layout.addWidget(self._album_lbl)
        right_layout.addSpacing(20)

        # Chips row
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

        # ── Lyrics header (hidden when no lyrics) ────────────────────────
        lyrics_hdr_row = QHBoxLayout()
        lyrics_hdr_row.setContentsMargins(0, 0, 0, 4)
        self._lyrics_hdr = QLabel("LYRICS")
        self._lyrics_hdr.setFont(QFont("Cambria", 10, QFont.Bold))
        self._lyrics_hdr.setStyleSheet(
            "color: rgba(133,153,234,0.55); letter-spacing: 2px;"
            " background: transparent; border: none;"
        )
        self._lyrics_hdr.setVisible(False)
        lyrics_hdr_row.addWidget(self._lyrics_hdr)
        lyrics_hdr_row.addStretch()
        right_layout.addLayout(lyrics_hdr_row)

        self._lyrics_divider = QFrame()
        self._lyrics_divider.setFrameShape(QFrame.HLine)
        self._lyrics_divider.setStyleSheet(
            "border: none; border-top: 1px solid rgba(133,153,234,0.18);"
            " background: transparent;"
        )
        self._lyrics_divider.setFixedHeight(1)
        self._lyrics_divider.setVisible(False)
        right_layout.addWidget(self._lyrics_divider)
        right_layout.addSpacing(10)

        # ── Lyrics body — always takes up space ──────────────────────────
        #
        # Both the karaoke label and the plain scroll area live in the same
        # stretch slot. Only one is visible at a time; when neither is shown
        # (no lyrics) the column keeps its full height so the layout is stable.

        # Karaoke: one big centred label, fades in on each line change
        self._karaoke_lbl = _KaraokeLine()
        self._karaoke_lbl.setVisible(False)

        # Plain: full scrollable text
        self._plain_area = _FadedScrollArea()
        self._plain_area.setStyleSheet("background: transparent; border: none;")
        self._plain_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._plain_lbl = QLabel()
        self._plain_lbl.setFont(self._PLAIN_FONT)
        self._plain_lbl.setStyleSheet(
            "color: rgba(200,208,244,0.75); line-height: 1.7em;"
            " background: transparent; border: none;"
        )
        self._plain_lbl.setWordWrap(True)
        self._plain_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._plain_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._plain_lbl.setContentsMargins(0, 8, 0, 24)
        self._plain_area.setWidget(self._plain_lbl)
        self._plain_area.setVisible(False)

        right_layout.addWidget(self._karaoke_lbl, stretch=1)
        right_layout.addWidget(self._plain_area, stretch=1)

        # ── Sync offset control (only visible in karaoke mode) ────────────
        # Lets the user nudge timestamps earlier/later to compensate for
        # inaccurate LRC files. Range: -5s to +5s in 100ms steps.
        self._offset_row = QWidget()
        self._offset_row.setStyleSheet("background: transparent;")
        offset_layout = QHBoxLayout(self._offset_row)
        offset_layout.setContentsMargins(0, 6, 0, 0)
        offset_layout.setSpacing(8)

        self._offset_lbl = QLabel("Sync  −0.5s")
        self._offset_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.45); font-size: 10px;"
            " background: transparent; border: none;"
        )
        self._offset_lbl.setFixedWidth(80)

        self._offset_slider = QSlider(Qt.Horizontal)
        self._offset_slider.setRange(-50, 50)  # steps of 100ms → −5s to +5s
        self._offset_slider.setValue(-5)  # default −500ms
        self._offset_slider.setTickInterval(5)
        self._offset_slider.setSingleStep(1)
        self._offset_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 2px;"
            " background: rgba(133,153,234,0.25); border-radius: 1px; }"
            " QSlider::handle:horizontal { width: 10px; height: 10px;"
            " margin: -4px 0; border-radius: 5px;"
            " background: rgba(133,153,234,0.60); }"
            " QSlider::sub-page:horizontal { background: rgba(133,153,234,0.50);"
            " border-radius: 1px; }"
        )
        self._offset_slider.valueChanged.connect(self._on_offset_changed)

        offset_layout.addWidget(self._offset_lbl)
        offset_layout.addWidget(self._offset_slider)
        self._offset_row.setVisible(False)
        right_layout.addWidget(self._offset_row)

        root.addWidget(left_widget, 42)
        root.addWidget(right_widget, 58)

    # ── resize ────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._backdrop.setGeometry(0, 0, self.width(), self.height())

    # ── public API ────────────────────────────────────────────────────────

    def updateUI(self, track):
        try:
            if not track:
                self.clearUI()
                return

            import time

            t0 = time.time()
            logger.info(f"NowPlayingView.updateUI: {getattr(track, 'track_name', '?')}")

            self._title_lbl.setText(
                getattr(track, "track_name", None) or "Unknown Title"
            )

            artists = getattr(track, "artists", None) or []
            self._artist_lbl.setText(
                getattr(artists[0], "artist_name", "") or "—" if artists else "—"
            )

            album = getattr(track, "album", None)
            self._album_lbl.setText(
                getattr(album, "album_name", "") or "—" if album else "—"
            )

            self._update_chips(track)
            self._update_lyrics(track)

            art_path = None
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

            logger.debug(f"updateUI TOTAL: {time.time() - t0:.3f}s")

        except Exception as e:
            logger.error(
                f"NowPlayingView.updateUI failed: {e}\n{traceback.format_exc()}"
            )
            self.clearUI()

    def clearUI(self):
        self._title_lbl.setText("No Track Playing")
        self._artist_lbl.setText("—")
        self._album_lbl.setText("—")
        self._set_lyrics_mode_none()
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

    # ── lyrics ────────────────────────────────────────────────────────────

    def _update_lyrics(self, track):
        """Parse lyrics and switch the display to the appropriate mode."""
        raw = getattr(track, "lyrics", None)

        # Reset sync state
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._last_position_ms = -1

        if not raw or not raw.strip():
            self._set_lyrics_mode_none()
            return

        is_synced, lines = _parse_lyrics(raw)
        self._lyrics_lines = lines
        self._is_synced = is_synced

        if is_synced:
            self._set_lyrics_mode_karaoke()
            # Show line at position 0 immediately
            first_text = lines[0][1] if lines else ""
            if first_text:
                self._karaoke_lbl.show_line(first_text)
            self._active_idx = 0
        else:
            plain_text = "\n".join(text for _, text in lines)
            self._set_lyrics_mode_plain(plain_text)

    def _set_lyrics_mode_none(self):
        """No lyrics — blank the area; header and divider hidden."""
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._lyrics_hdr.setVisible(False)
        self._lyrics_divider.setVisible(False)
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._plain_area.setVisible(False)
        self._plain_lbl.setText("")
        self._offset_row.setVisible(False)

    def _set_lyrics_mode_karaoke(self):
        """Synced lyrics — show karaoke label and offset slider."""
        self._lyrics_hdr.setVisible(True)
        self._lyrics_divider.setVisible(True)
        self._plain_area.setVisible(False)
        self._karaoke_lbl.setVisible(True)
        self._offset_slider.setValue(-5)  # reset to default −500ms on each new track
        self._offset_row.setVisible(True)

    def _set_lyrics_mode_plain(self, text: str):
        """Plain lyrics — show scrollable text only."""
        self._lyrics_hdr.setVisible(True)
        self._lyrics_divider.setVisible(True)
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._offset_row.setVisible(False)
        self._plain_lbl.setText(text)
        self._plain_area.setVisible(True)
        self._plain_area.verticalScrollBar().setValue(0)

    # ── position sync ─────────────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        """
        Fired ~every 50 ms by MusicPlayer. Throttled to 150 ms.
        Updates the karaoke label when the active line changes.
        """
        if not self._is_synced or not self._lyrics_lines:
            return
        if abs(position_ms - self._last_position_ms) < 150:
            return
        self._last_position_ms = position_ms

        new_idx = _active_index(self._lyrics_lines, position_ms + self._sync_offset_ms)
        if new_idx == self._active_idx:
            return

        self._active_idx = new_idx
        line_text = self._lyrics_lines[new_idx][1]

        # Skip blank/instrumental marker lines — keep showing the previous line
        if line_text.strip():
            self._karaoke_lbl.show_line(line_text)

    def _on_offset_changed(self, value: int):
        """Slider moved — update offset and force a lyric re-check."""
        self._sync_offset_ms = value * 100  # each step = 100 ms
        seconds = self._sync_offset_ms / 1000
        sign = "+" if seconds >= 0 else "−"
        self._offset_lbl.setText(f"Sync  {sign}{abs(seconds):.1f}s")
        # Force re-evaluation at the current position
        self._last_position_ms = -1

    # ── chips + art ───────────────────────────────────────────────────────

    def _update_chips(self, track):
        dur = getattr(track, "duration", None)
        if dur:
            mins, secs = int(dur) // 60, int(dur) % 60
            self._chip_duration.set_value(f"{mins}:{secs:02d}")
            self._chip_duration.setVisible(True)
        else:
            self._chip_duration.setVisible(False)

        bpm = getattr(track, "bpm", None)
        if bpm:
            self._chip_bpm.set_value(f"{float(bpm):.0f} BPM")
            self._chip_bpm.setVisible(True)
        else:
            self._chip_bpm.setVisible(False)

        key = getattr(track, "key", None)
        if key:
            mode = getattr(track, "mode", "") or ""
            self._chip_key.set_value(f"{key} {mode}".strip())
            self._chip_key.setVisible(True)
        else:
            self._chip_key.setVisible(False)

        bitrate = getattr(track, "bit_rate", None)
        if bitrate:
            self._chip_bitrate.set_value(f"{int(bitrate)} kbps")
            self._chip_bitrate.setVisible(True)
        else:
            self._chip_bitrate.setVisible(False)

        track_no = getattr(track, "track_number", None)
        if track_no:
            self._chip_track_no.set_value(f"Track {track_no}")
            self._chip_track_no.setVisible(True)
        else:
            self._chip_track_no.setVisible(False)

    def _load_art(self, pixmap: Optional[QPixmap]):
        self._current_pixmap = pixmap
        self._art_card.set_art(pixmap)

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
