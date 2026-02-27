"""
NowPlayingView module — Cinematic redesign.

A full-bleed, immersive "now playing" experience:
  - Album art dominates the left half as a large, softly-shadowed card
  - A blurred, colour-extracted backdrop bleeds behind everything
  - Track title / artist / album in refined, layered typography
  - Pill-shaped metadata chips (BPM, Key, Duration, Bitrate…)
  - Scrollable lyrics panel with fade-in/out at the edges
  - Smooth cross-fade when the track changes via QPropertyAnimation
  - Lyrics sync: timestamped lyrics ([mm:ss] or [mm:ss.xx]) scroll and
    highlight the current line in real time. Plain lyrics are shown as-is.
    The entire lyrics section is hidden when a track has no lyrics.
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
    QVBoxLayout,
    QWidget,
)

from src.asset_paths import asset
from src.logger_config import logger

# ──────────────────────────────────────────────────────────────────────────────
#  Lyrics parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

# Matches [mm:ss], [mm:ss.xx], [mm:ss.xxx]
_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.,](\d+))?\](.*)$")


def _parse_lyrics(raw: str) -> Tuple[bool, List[Tuple[int, str]]]:
    """
    Try to parse lyrics as timestamped LRC format.

    Returns:
        (is_synced, lines)
        - is_synced: True if at least one timestamp was found
        - lines: list of (start_ms, text) sorted by time.
          If not synced, returns [(0, full_text)] so callers can treat
          both modes uniformly.
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
            # Normalise fractional part to milliseconds
            if len(frac) <= 2:
                ms_frac = int(frac) * (100 if len(frac) == 1 else 10)
            else:
                ms_frac = int(frac[:3])
            total_ms = (mins * 60 + secs) * 1000 + ms_frac
            text = m.group(4).strip()
            parsed.append((total_ms, text))

    if parsed:
        parsed.sort(key=lambda x: x[0])
        return True, parsed

    # Plain text — return as a single block
    return False, [(0, raw.strip())]


def _active_line_index(lines: List[Tuple[int, str]], position_ms: int) -> int:
    """Return the index of the line that should be highlighted at position_ms."""
    active = 0
    for i, (ts, _) in enumerate(lines):
        if ts <= position_ms:
            active = i
        else:
            break
    return active


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
            # Scale to fill, then apply a heavy dark overlay
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
    """Rounded album art card with soft drop shadow."""

    RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._art: Optional[QPixmap] = None
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_art(self, pixmap: Optional[QPixmap]):
        self._art = pixmap
        self._art_cache: Optional[QPixmap] = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        r = self.RADIUS

        if self._art and not self._art.isNull():
            side = min(w, h) - 24
            x = (w - side) // 2
            y = (h - side) // 2

            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(x + 4, y + 8, side, side, r, r)
            painter.fillPath(shadow_path, QColor(0, 0, 0, 120))

            clip = QPainterPath()
            clip.addRoundedRect(x, y, side, side, r, r)
            painter.setClipPath(clip)

            if (
                not hasattr(self, "_art_cache")
                or self._art_cache is None
                or self._art_cache.width() != side
            ):
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
#  Synced lyrics widget
# ──────────────────────────────────────────────────────────────────────────────

# Colours used for lyric lines
_COLOUR_ACTIVE = "rgba(255, 255, 255, 0.97)"  # bright white — current line
_COLOUR_NEAR = "rgba(200, 208, 244, 0.60)"  # soft blue-white — adjacent lines
_COLOUR_DIM = "rgba(200, 208, 244, 0.28)"  # faded — everything else


class _SyncedLyricsWidget(QWidget):
    """
    Renders each lyric line as its own QLabel so we can style them
    individually.  The active line is bright and slightly larger; nearby
    lines fade toward the edges.

    For plain (non-timestamped) lyrics a single label is used and no
    position tracking is performed.
    """

    _LINE_FONT_ACTIVE = QFont("Cambria", 14, QFont.DemiBold)
    _LINE_FONT_NORMAL = QFont("Cambria", 12, QFont.Normal)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 24)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignTop)

        self._line_labels: List[QLabel] = []
        self._is_synced = False
        self._lines: List[Tuple[int, str]] = []  # (ms, text)
        self._active_idx = -1

    # ── public API ────────────────────────────────────────────────────────

    def set_lyrics(self, is_synced: bool, lines: List[Tuple[int, str]]):
        """Replace all content with a new set of lyrics."""
        self._is_synced = is_synced
        self._lines = lines
        self._active_idx = -1
        self._rebuild_labels()

    def update_position(self, position_ms: int):
        """Called on every position_changed tick. No-op for plain lyrics."""
        if not self._is_synced or not self._lines:
            return
        new_idx = _active_line_index(self._lines, position_ms)
        if new_idx != self._active_idx:
            self._active_idx = new_idx
            self._restyle_labels()

    def clear(self):
        self._lines = []
        self._line_labels = []
        self._is_synced = False
        self._active_idx = -1
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── internals ─────────────────────────────────────────────────────────

    def _rebuild_labels(self):
        """Tear down and recreate all line labels from scratch."""
        self.clear()

        if not self._lines:
            return

        if not self._is_synced:
            # Single label for plain text
            lbl = QLabel(self._lines[0][1])
            lbl.setFont(self._LINE_FONT_NORMAL)
            lbl.setStyleSheet(
                f"color: {_COLOUR_NEAR}; background: transparent; border: none;"
            )
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self._layout.addWidget(lbl)
            self._line_labels = [lbl]
            return

        # One label per synced line
        for _ms, text in self._lines:
            lbl = QLabel(text if text else " ")  # blank lines need height
            lbl.setFont(self._LINE_FONT_NORMAL)
            lbl.setStyleSheet(
                f"color: {_COLOUR_DIM}; background: transparent; border: none;"
            )
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self._layout.addWidget(lbl)
            self._line_labels.append(lbl)

        # Kick off initial styling
        self._restyle_labels()

    def _restyle_labels(self):
        """Apply highlight/dim styling relative to the active line index."""
        if not self._is_synced:
            return
        n = len(self._line_labels)
        for i, lbl in enumerate(self._line_labels):
            dist = abs(i - self._active_idx)
            if dist == 0:
                colour = _COLOUR_ACTIVE
                font = self._LINE_FONT_ACTIVE
            elif dist <= 2:
                colour = _COLOUR_NEAR
                font = self._LINE_FONT_NORMAL
            else:
                colour = _COLOUR_DIM
                font = self._LINE_FONT_NORMAL
            lbl.setFont(font)
            lbl.setStyleSheet(
                f"color: {colour}; background: transparent; border: none;"
            )

        # Scroll the active label into the centre of the viewport
        if 0 <= self._active_idx < n:
            self._scroll_to_active()

    def _scroll_to_active(self):
        """Ask the parent _FadedScrollArea to centre the active label."""
        lbl = self._line_labels[self._active_idx]
        # Walk up to find the QScrollArea
        scroll_area = self.parent()
        if not isinstance(scroll_area, QScrollArea):
            # _SyncedLyricsWidget is set as the widget of the scroll area,
            # so its parent is the scroll area's viewport, and that parent
            # is the scroll area itself.
            scroll_area = self.parent().parent() if self.parent() else None
        if not isinstance(scroll_area, QScrollArea):
            return

        # Target: centre of label relative to this widget
        lbl_top = lbl.mapTo(self, lbl.rect().topLeft()).y()
        lbl_centre = lbl_top + lbl.height() // 2
        viewport_h = scroll_area.viewport().height()
        target_scroll = max(0, lbl_centre - viewport_h // 2)

        bar = scroll_area.verticalScrollBar()
        if bar:
            # Smooth-scroll by stepping toward the target
            current = bar.value()
            step = (target_scroll - current) // 3
            if abs(step) > 1:
                bar.setValue(current + step)
            else:
                bar.setValue(target_scroll)


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
        self._current_pixmap: Optional[QPixmap] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        # Lyrics state
        self._synced_lyrics: List[Tuple[int, str]] = []
        self._is_synced = False

        # Throttle: only re-highlight once every 200 ms (well above 50 ms ticks)
        self._last_sync_ms: int = -1

        self._initUI()

        # Connect directly to the player's position signal — no wiring needed
        # in main_window.py
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

        self._art_card = _ArtCard()
        self._art_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout.addWidget(self._art_card)

        # ── RIGHT — metadata + lyrics column ────────────────────────────
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

        # ── Lyrics section (hidden when no lyrics) ───────────────────────
        self._lyrics_section = QWidget()
        self._lyrics_section.setStyleSheet("background: transparent;")
        lyrics_section_layout = QVBoxLayout(self._lyrics_section)
        lyrics_section_layout.setContentsMargins(0, 0, 0, 0)
        lyrics_section_layout.setSpacing(0)

        # "LYRICS" header row
        lyrics_header_row = QHBoxLayout()
        lyrics_header_row.setContentsMargins(0, 0, 0, 4)

        self._lyrics_section_lbl = QLabel("LYRICS")
        self._lyrics_section_lbl.setFont(QFont("Cambria", 10, QFont.Bold))
        self._lyrics_section_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.55); letter-spacing: 2px;"
            " background: transparent; border: none;"
        )
        lyrics_header_row.addWidget(self._lyrics_section_lbl)

        # Sync indicator — only visible when lyrics are timestamped
        self._sync_indicator = QLabel("● SYNCED")
        self._sync_indicator.setFont(QFont("Cambria", 9, QFont.Normal))
        self._sync_indicator.setStyleSheet(
            "color: rgba(100, 220, 140, 0.70); letter-spacing: 1px;"
            " background: transparent; border: none;"
        )
        self._sync_indicator.setVisible(False)
        lyrics_header_row.addWidget(self._sync_indicator)
        lyrics_header_row.addStretch()

        lyrics_section_layout.addLayout(lyrics_header_row)

        # Thin divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(
            "border: none; border-top: 1px solid rgba(133,153,234,0.18);"
            " background: transparent;"
        )
        divider.setFixedHeight(1)
        lyrics_section_layout.addWidget(divider)
        lyrics_section_layout.addSpacing(10)

        # Scrollable synced-lyrics widget
        self._lyrics_area = _FadedScrollArea()
        self._lyrics_area.setStyleSheet("background: transparent; border: none;")
        self._lyrics_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._synced_widget = _SyncedLyricsWidget()
        self._lyrics_area.setWidget(self._synced_widget)

        lyrics_section_layout.addWidget(self._lyrics_area)

        right_layout.addWidget(self._lyrics_section)

        # Hide by default — shown only when lyrics are present
        self._lyrics_section.setVisible(False)

        # ── assemble root ──
        root.addWidget(left_widget, 42)
        root.addWidget(right_widget, 58)

    # ── resize: keep backdrop full size ───────────────────────────────────

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

            title = getattr(track, "track_name", None) or "Unknown Title"
            self._title_lbl.setText(title)

            t1 = time.time()
            artists = getattr(track, "artists", None) or []
            artist_name = ""
            if artists:
                artist_name = getattr(artists[0], "artist_name", "") or ""
            self._artist_lbl.setText(artist_name or "—")
            logger.debug(f"updateUI: artists took {time.time() - t1:.3f}s")

            t2 = time.time()
            album = getattr(track, "album", None)
            album_name = ""
            if album:
                album_name = getattr(album, "album_name", "") or ""
            self._album_lbl.setText(album_name or "—")
            logger.debug(f"updateUI: album took {time.time() - t2:.3f}s")

            t3 = time.time()
            self._update_chips(track)
            logger.debug(f"updateUI: chips took {time.time() - t3:.3f}s")

            t4 = time.time()
            self._update_lyrics(track)
            logger.debug(f"updateUI: lyrics took {time.time() - t4:.3f}s")

            t5 = time.time()
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
            logger.debug(f"updateUI: art took {time.time() - t5:.3f}s")
            logger.debug(f"updateUI: TOTAL took {time.time() - t0:.3f}s")

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
        self._lyrics_section.setVisible(False)
        self._synced_widget.clear()
        self._synced_lyrics = []
        self._is_synced = False
        self._last_sync_ms = -1

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

    # ── lyrics handling ───────────────────────────────────────────────────

    def _update_lyrics(self, track):
        """
        Parse lyrics from the track and configure the lyrics section.
        Hides the section entirely when no lyrics exist.
        """
        raw = getattr(track, "lyrics", None)

        # Reset sync state for the new track
        self._synced_lyrics = []
        self._is_synced = False
        self._last_sync_ms = -1
        self._synced_widget.clear()

        if not raw or not raw.strip():
            # No lyrics — hide the whole section
            self._lyrics_section.setVisible(False)
            return

        is_synced, lines = _parse_lyrics(raw)

        self._is_synced = is_synced
        self._synced_lyrics = lines
        self._synced_widget.set_lyrics(is_synced, lines)
        self._sync_indicator.setVisible(is_synced)
        self._lyrics_section.setVisible(True)

        # Scroll back to top for the new track
        self._lyrics_area.verticalScrollBar().setValue(0)

    # ── player position tick ──────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        """
        Fired every ~50 ms by MusicPlayer.position_changed.
        We throttle to every 200 ms to avoid unnecessary work.
        """
        if not self._is_synced or not self._synced_lyrics:
            return
        if abs(position_ms - self._last_sync_ms) < 200:
            return
        self._last_sync_ms = position_ms
        self._synced_widget.update_position(position_ms)

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

    def _load_art(self, pixmap: Optional[QPixmap]):
        """Update album art and animate the backdrop into view."""
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
