"""
NowPlayingView module — Cinematic redesign.

Layout is always two columns (art | metadata+content). The content panel
toggles between LYRICS and CREDITS via tab buttons above it.

Lyrics modes:
  - No lyrics        → placeholder message shown
  - Plain text       → full scrollable text, no timestamps shown to user
  - Synced (LRC)     → karaoke style: only the CURRENT line is shown,
                       centred, large, fading in on each change.
                       Timestamps are never visible to the user.

Credits mode:
  - Stacked cards, one per artist/role pair (excludes Primary Artist)
  - Auto-scrolls vertically like movie credits when content overflows
"""

import re
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
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


class _ScrollingChipRow(QWidget):
    """
    Horizontal chip strip that auto-scrolls left→right→left when chips
    overflow the available width. No scrollbar shown — ticks silently.
    """

    _SPEED_PX = 1
    _TICK_MS = 28
    _PAUSE_MS = 2200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(34)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setStyleSheet("background: transparent; border: none;")
        self._area.setFixedHeight(34)

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._area.setWidget(self._inner)

        outer.addWidget(self._area)

        self._chips: List[_Chip] = []
        self._offset = 0
        self._direction = 1
        self._paused = True

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_chips(self, chips: List[_Chip]):
        for ch in self._chips:
            self._row.removeWidget(ch)
            ch.setParent(None)
        self._chips = []

        for ch in chips:
            self._row.addWidget(ch)
            self._chips.append(ch)

        self._row.addStretch()
        self._inner.adjustSize()

        self._offset = 0
        self._direction = 1
        self._paused = True
        self._area.horizontalScrollBar().setValue(0)
        self._timer.stop()

        QTimer.singleShot(self._PAUSE_MS, self._maybe_start)

    def _maybe_start(self):
        if self._area.horizontalScrollBar().maximum() > 0:
            self._paused = False
            self._timer.start()

    def _tick(self):
        if self._paused:
            return
        sb = self._area.horizontalScrollBar()
        max_val = sb.maximum()
        if max_val <= 0:
            self._timer.stop()
            return

        self._offset = max(
            0, min(self._offset + self._direction * self._SPEED_PX, max_val)
        )
        sb.setValue(self._offset)

        if self._offset >= max_val or self._offset <= 0:
            self._paused = True
            QTimer.singleShot(self._PAUSE_MS, self._flip)

    def _flip(self):
        self._direction *= -1
        self._paused = False


# ──────────────────────────────────────────────────────────────────────────────
#  Credits panel
# ──────────────────────────────────────────────────────────────────────────────

_ROLE_PALETTE = {
    "composer": "#e8a87c",
    "lyricist": "#a8e0b0",
    "original lyricist": "#a8e0b0",
    "conductor": "#c8a0e8",
    "arranger": "#80c8e8",
    "producer": "#e8c880",
    "engineer": "#80e8c8",
    "mixer": "#e880a8",
    "mastering engineer": "#a0b8e8",
    "album artist": "#8599ea",
    "original performer": "#e8a87c",
    "featured artist": "#f0c0d0",
    "piano": "#c8d8f8",
    "guitar": "#f8d0a0",
    "bass": "#d0f0c8",
    "drums": "#f8c8c8",
    "vocals": "#f8e8c0",
    "saxophone": "#c8e8f8",
    "trumpet": "#f8e0a8",
    "violin": "#e8c8f0",
}
_ROLE_DEFAULT = "#8599ea"


class _CreditCard(QFrame):
    """
    One card per artist/role pair. Left-coloured border, role label right-aligned,
    artist name large on the right.
    """

    def __init__(self, role_name: str, artist_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("CreditCard")

        colour = _ROLE_PALETTE.get(role_name.lower(), _ROLE_DEFAULT)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame#CreditCard {{
                background: rgba(133, 153, 234, 0.07);
                border: 1px solid rgba(133, 153, 234, 0.16);
                border-left: 3px solid {colour};
                border-radius: 8px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 16, 10)
        lay.setSpacing(12)

        role_lbl = QLabel(role_name.upper())
        role_lbl.setFont(QFont("Cambria", 8, QFont.Bold))
        role_lbl.setStyleSheet(
            f"color: {colour}; letter-spacing: 1.8px;"
            " background: transparent; border: none;"
        )
        role_lbl.setFixedWidth(148)
        role_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        sep = QLabel("·")
        sep.setStyleSheet(
            "color: rgba(133,153,234,0.28); font-size: 16px;"
            " background: transparent; border: none;"
        )
        sep.setAlignment(Qt.AlignCenter)
        sep.setFixedWidth(18)

        artist_lbl = QLabel(artist_name)
        artist_lbl.setFont(QFont("Georgia", 15))
        artist_lbl.setStyleSheet(
            "color: rgba(230, 235, 255, 0.92); background: transparent; border: none;"
        )
        artist_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        lay.addWidget(role_lbl)
        lay.addWidget(sep)
        lay.addWidget(artist_lbl)


class _CreditsPanel(QWidget):
    """
    Scrollable stack of _CreditCard widgets. Auto-scrolls like movie credits
    when content overflows, reverses, loops.
    """

    _SPEED = 0.55
    _TICK_MS = 40
    _PAUSE_MS = 2800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setStyleSheet("background: transparent; border: none;")
        self._area.setWidgetResizable(True)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 8, 0, 48)
        self._cards_layout.setSpacing(6)
        self._cards_layout.setAlignment(Qt.AlignTop)
        self._area.setWidget(self._container)

        root.addWidget(self._area)

        self._pos: float = 0.0
        self._direction = 1
        self._paused = True

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

    def stop(self):
        self._timer.stop()

    def load_credits(self, track):
        self._timer.stop()
        self._pos = 0.0
        self._direction = 1
        self._paused = True
        self._area.verticalScrollBar().setValue(0)

        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not track:
            self._show_placeholder("No track loaded")
            return

        rows: List[Tuple[str, str]] = []
        try:
            for ar in getattr(track, "artist_roles", None) or []:
                role = getattr(ar, "role", None)
                artist = getattr(ar, "artist", None)
                if not role or not artist:
                    continue
                rname = (getattr(role, "role_name", "") or "").strip()
                aname = (getattr(artist, "artist_name", "") or "").strip()
                if rname.lower() == "primary artist" or not rname or not aname:
                    continue
                rows.append((rname, aname))
        except Exception as exc:
            logger.warning(f"_CreditsPanel.load_credits: {exc}")

        if not rows:
            self._show_placeholder("No credits available")
            return

        rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))

        for rname, aname in rows:
            self._cards_layout.addWidget(_CreditCard(rname, aname))

        self._cards_layout.addStretch()
        QTimer.singleShot(self._PAUSE_MS, self._maybe_start)

    def _show_placeholder(self, msg: str):
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "color: rgba(133,153,234,0.28); font-size: 14px; font-style: italic;"
            " background: transparent; border: none;"
        )
        self._cards_layout.addStretch()
        self._cards_layout.addWidget(lbl)
        self._cards_layout.addStretch()

    def _maybe_start(self):
        if self._area.verticalScrollBar().maximum() > 0:
            self._paused = False
            self._timer.start()

    def _tick(self):
        if self._paused:
            return
        sb = self._area.verticalScrollBar()
        top = sb.maximum()
        if top <= 0:
            self._timer.stop()
            return

        self._pos = max(0.0, min(self._pos + self._direction * self._SPEED, float(top)))
        sb.setValue(int(self._pos))

        if self._pos >= top or self._pos <= 0.0:
            self._paused = True
            QTimer.singleShot(self._PAUSE_MS, self._flip)

    def _flip(self):
        self._direction *= -1
        self._paused = False


# ──────────────────────────────────────────────────────────────────────────────
#  Faded scroll area (plain lyrics)
# ──────────────────────────────────────────────────────────────────────────────


class _FadedScrollArea(QScrollArea):
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
#  Karaoke line
# ──────────────────────────────────────────────────────────────────────────────


class _KaraokeLine(QLabel):
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
#  Tab button styles
# ──────────────────────────────────────────────────────────────────────────────

_TAB_ACTIVE = """
    QPushButton {
        background: rgba(133, 153, 234, 0.20);
        border: 1px solid rgba(133, 153, 234, 0.55);
        border-bottom: none;
        border-radius: 0px;
        color: rgba(230, 235, 255, 0.92);
        font-size: 10px;
        font-weight: bold;
        letter-spacing: 2px;
        padding: 5px 22px;
    }
"""

_TAB_INACTIVE = """
    QPushButton {
        background: transparent;
        border: 1px solid rgba(133, 153, 234, 0.14);
        border-bottom: none;
        border-radius: 0px;
        color: rgba(133, 153, 234, 0.42);
        font-size: 10px;
        font-weight: bold;
        letter-spacing: 2px;
        padding: 5px 22px;
    }
    QPushButton:hover {
        background: rgba(133, 153, 234, 0.09);
        color: rgba(200, 208, 244, 0.70);
    }
"""


# ──────────────────────────────────────────────────────────────────────────────
#  Main view
# ──────────────────────────────────────────────────────────────────────────────


class NowPlayingView(QWidget):
    """Cinematic now-playing view with blurred backdrop and rich metadata."""

    _TITLE_FONT = QFont("Georgia", 28, QFont.Bold)
    _ARTIST_FONT = QFont("Cambria", 16, QFont.Normal)
    _ALBUM_FONT = QFont("Cambria", 13, QFont.Normal)
    _PLAIN_FONT = QFont("Cambria", 12, QFont.Normal)

    _PAGE_LYRICS = 0
    _PAGE_CREDITS = 1

    def __init__(self, controller, track=None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self._current_pixmap: Optional[QPixmap] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        self._is_synced = False
        self._lyrics_lines: List[Tuple[int, str]] = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._sync_offset_ms = -500

        self._initUI()

        try:
            self.controller.mediaplayer.position_changed.connect(
                self._on_position_changed
            )
        except Exception as exc:
            logger.warning(f"NowPlayingView: could not connect position_changed: {exc}")

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

        # ── RIGHT — metadata + content panel ────────────────────────────
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

        # Album  (release year appended at runtime)
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setStyleSheet(
            "color: rgba(200,208,244,0.50); background: transparent; border: none;"
        )
        self._album_lbl.setWordWrap(True)
        right_layout.addWidget(self._album_lbl)
        right_layout.addSpacing(16)

        # Chip objects (created here, populated in _update_chips)
        self._chip_duration = _Chip("⏱", "—")
        self._chip_track_no = _Chip("#", "—")
        self._chip_bpm = _Chip("♩", "—")
        self._chip_key = _Chip("𝄞", "—")
        self._chip_timesig = _Chip("𝄴", "—")
        self._chip_bitrate = _Chip("≋", "—")
        self._chip_sample = _Chip("Hz", "—")
        self._chip_depth = _Chip("bit", "—")
        self._chip_rec_year = _Chip("📅", "—")
        self._chip_plays = _Chip("▶", "—")
        self._chip_rating = _Chip("★", "—")
        self._chip_genres = _Chip("🎵", "—")

        self._chip_row = _ScrollingChipRow()
        right_layout.addWidget(self._chip_row)
        right_layout.addSpacing(14)

        # ── Tab bar ───────────────────────────────────────────────────────
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.setSpacing(0)

        self._tab_lyrics = QPushButton("LYRICS")
        self._tab_credits = QPushButton("CREDITS")
        for btn in (self._tab_lyrics, self._tab_credits):
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)

        self._tab_lyrics.setStyleSheet(_TAB_ACTIVE)
        self._tab_credits.setStyleSheet(_TAB_INACTIVE)

        self._tab_lyrics.clicked.connect(lambda: self._switch_tab(self._PAGE_LYRICS))
        self._tab_credits.clicked.connect(lambda: self._switch_tab(self._PAGE_CREDITS))

        tab_bar.addWidget(self._tab_lyrics)
        tab_bar.addWidget(self._tab_credits)
        tab_bar.addStretch()
        right_layout.addLayout(tab_bar)

        tab_rule = QFrame()
        tab_rule.setFrameShape(QFrame.HLine)
        tab_rule.setStyleSheet(
            "border: none; border-top: 1px solid rgba(133,153,234,0.25);"
            " background: transparent;"
        )
        tab_rule.setFixedHeight(1)
        right_layout.addWidget(tab_rule)
        right_layout.addSpacing(10)

        # ── Stacked pages ─────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Page 0: LYRICS
        lyrics_page = QWidget()
        lyrics_page.setStyleSheet("background: transparent;")
        lp = QVBoxLayout(lyrics_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)

        self._karaoke_lbl = _KaraokeLine()
        self._karaoke_lbl.setVisible(False)

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

        self._no_lyrics_lbl = QLabel("No lyrics available")
        self._no_lyrics_lbl.setAlignment(Qt.AlignCenter)
        self._no_lyrics_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.28); font-size: 14px; font-style: italic;"
            " background: transparent; border: none;"
        )
        self._no_lyrics_lbl.setVisible(False)

        lp.addWidget(self._karaoke_lbl, stretch=1)
        lp.addWidget(self._plain_area, stretch=1)
        lp.addWidget(self._no_lyrics_lbl, stretch=1)

        # Sync offset slider
        self._offset_row = QWidget()
        self._offset_row.setStyleSheet("background: transparent;")
        off_lay = QHBoxLayout(self._offset_row)
        off_lay.setContentsMargins(0, 6, 0, 0)
        off_lay.setSpacing(8)

        self._offset_lbl = QLabel("Sync  −0.5s")
        self._offset_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.45); font-size: 10px;"
            " background: transparent; border: none;"
        )
        self._offset_lbl.setFixedWidth(80)

        self._offset_slider = QSlider(Qt.Horizontal)
        self._offset_slider.setRange(-50, 50)
        self._offset_slider.setValue(-5)
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

        off_lay.addWidget(self._offset_lbl)
        off_lay.addWidget(self._offset_slider)
        self._offset_row.setVisible(False)
        lp.addWidget(self._offset_row)

        # Page 1: CREDITS
        self._credits_panel = _CreditsPanel()

        self._stack.addWidget(lyrics_page)
        self._stack.addWidget(self._credits_panel)

        right_layout.addWidget(self._stack, stretch=1)

        root.addWidget(left_widget, 42)
        root.addWidget(right_widget, 58)

    # ── tab switching ──────────────────────────────────────────────────────

    def _switch_tab(self, page: int):
        self._stack.setCurrentIndex(page)
        if page == self._PAGE_LYRICS:
            self._tab_lyrics.setStyleSheet(_TAB_ACTIVE)
            self._tab_credits.setStyleSheet(_TAB_INACTIVE)
            self._credits_panel.stop()
        else:
            self._tab_lyrics.setStyleSheet(_TAB_INACTIVE)
            self._tab_credits.setStyleSheet(_TAB_ACTIVE)
            self._credits_panel.load_credits(self.track)

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

            t0 = time.time()
            logger.info(f"NowPlayingView.updateUI: {getattr(track, 'track_name', '?')}")

            self.track = track

            self._title_lbl.setText(
                getattr(track, "track_name", None) or "Unknown Title"
            )

            artists = getattr(track, "artists", None) or []
            self._artist_lbl.setText(
                getattr(artists[0], "artist_name", "") or "—" if artists else "—"
            )

            album = getattr(track, "album", None)
            if album:
                name = getattr(album, "album_name", "") or "—"
                year = getattr(album, "release_year", None)
                self._album_lbl.setText(f"{name}  ({year})" if year else name)
            else:
                self._album_lbl.setText("—")

            self._update_chips(track)
            self._update_lyrics(track)

            if self._stack.currentIndex() == self._PAGE_CREDITS:
                self._credits_panel.load_credits(track)

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

        except Exception as exc:
            logger.error(
                f"NowPlayingView.updateUI failed: {exc}\n{traceback.format_exc()}"
            )
            self.clearUI()

    def clearUI(self):
        self.track = None
        self._title_lbl.setText("No Track Playing")
        self._artist_lbl.setText("—")
        self._album_lbl.setText("—")
        self._set_lyrics_mode_none()
        self._credits_panel.load_credits(None)
        self._chip_row.set_chips([])
        if self.default_art_path and Path(self.default_art_path).exists():
            self._load_art(QPixmap(self.default_art_path))
        else:
            self._load_art(None)

    # ── lyrics ────────────────────────────────────────────────────────────

    def _update_lyrics(self, track):
        raw = getattr(track, "lyrics", None)

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
            first = lines[0][1] if lines else ""
            if first:
                self._karaoke_lbl.show_line(first)
            self._active_idx = 0
        else:
            self._set_lyrics_mode_plain("\n".join(t for _, t in lines))

    def _set_lyrics_mode_none(self):
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._plain_area.setVisible(False)
        self._plain_lbl.setText("")
        self._no_lyrics_lbl.setVisible(True)
        self._offset_row.setVisible(False)

    def _set_lyrics_mode_karaoke(self):
        self._plain_area.setVisible(False)
        self._no_lyrics_lbl.setVisible(False)
        self._karaoke_lbl.setVisible(True)
        self._offset_slider.setValue(-5)
        self._offset_row.setVisible(True)

    def _set_lyrics_mode_plain(self, text: str):
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._no_lyrics_lbl.setVisible(False)
        self._offset_row.setVisible(False)
        self._plain_lbl.setText(text)
        self._plain_area.setVisible(True)
        self._plain_area.verticalScrollBar().setValue(0)

    # ── position sync ─────────────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        if not self._is_synced or not self._lyrics_lines:
            return
        if abs(position_ms - self._last_position_ms) < 150:
            return
        self._last_position_ms = position_ms

        new_idx = _active_index(self._lyrics_lines, position_ms + self._sync_offset_ms)
        if new_idx == self._active_idx:
            return
        self._active_idx = new_idx
        text = self._lyrics_lines[new_idx][1]
        if text.strip():
            self._karaoke_lbl.show_line(text)

    def _on_offset_changed(self, value: int):
        self._sync_offset_ms = value * 100
        secs = self._sync_offset_ms / 1000
        sign = "+" if secs >= 0 else "−"
        self._offset_lbl.setText(f"Sync  {sign}{abs(secs):.1f}s")
        self._last_position_ms = -1

    # ── chips ─────────────────────────────────────────────────────────────

    def _update_chips(self, track):
        visible: List[_Chip] = []

        def _maybe(chip: _Chip, val: Optional[str]):
            if val:
                chip.set_value(val)
                visible.append(chip)

        dur = getattr(track, "duration", None)
        if dur:
            m, s = int(dur) // 60, int(dur) % 60
            _maybe(self._chip_duration, f"{m}:{s:02d}")

        _maybe(
            self._chip_track_no,
            f"Track {getattr(track, 'track_number', None)}"
            if getattr(track, "track_number", None)
            else None,
        )

        bpm = getattr(track, "bpm", None)
        _maybe(self._chip_bpm, f"{float(bpm):.0f} BPM" if bpm else None)

        key = getattr(track, "key", None)
        if key:
            mode = getattr(track, "mode", "") or ""
            _maybe(self._chip_key, f"{key} {mode}".strip())

        ts = getattr(track, "primary_time_signature", None)
        _maybe(self._chip_timesig, str(ts) if ts else None)

        br = getattr(track, "bit_rate", None)
        _maybe(self._chip_bitrate, f"{int(br)} kbps" if br else None)

        sr = getattr(track, "sample_rate", None)
        _maybe(self._chip_sample, f"{int(sr) // 1000} kHz" if sr else None)

        bd = getattr(track, "bit_depth", None)
        _maybe(self._chip_depth, f"{int(bd)}-bit" if bd else None)

        ry = getattr(track, "recorded_year", None)
        _maybe(self._chip_rec_year, str(ry) if ry else None)

        plays = getattr(track, "play_count", None)
        _maybe(self._chip_plays, f"{int(plays)} plays" if plays else None)

        rating = getattr(track, "user_rating", None)
        _maybe(self._chip_rating, f"{float(rating):.1f}/10" if rating else None)

        genres = getattr(track, "genres", None) or []
        if genres:
            names = [getattr(g, "genre_name", "") for g in genres[:3]]
            gstr = ", ".join(n for n in names if n)
            _maybe(self._chip_genres, gstr or None)

        self._chip_row.set_chips(visible)

    # ── art ───────────────────────────────────────────────────────────────

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
