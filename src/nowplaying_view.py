"""
NowPlayingView module — Cinematic redesign.
"""

import re
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QShortcut,
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
from src.config_setup import app_config
from src.logger_config import logger
from src.nowplaying_art import _ArtCard
from src.nowplaying_backdrop import _BlurredBackdrop
from src.nowplaying_chip import _Chip, _ScrollingChipRow
from src.nowplaying_credits import _CreditsPanel
from src.nowplaying_karaoke import _KaraokeLine

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

# If the next lyric line starts more than this many ms in the future, show a
# countdown timer instead of a blank karaoke display.
_LYRIC_GAP_THRESHOLD_MS = 5_000

# Debounce delay (ms) before persisting the sync-offset slider to config.
_OFFSET_DEBOUNCE_MS = 600

# ──────────────────────────────────────────────────────────────────────────────
#  Lyrics parsing
# ──────────────────────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.,](\d+))?\](.*)")


def _parse_lyrics(raw: str) -> Tuple[bool, List[Tuple[int, str]]]:
    """
    Parse raw lyrics string.

    Returns (is_synced, lines) where lines is a list of (timestamp_ms, text).
    For plain lyrics, all timestamps are 0.
    """
    lines = []
    is_synced = False
    for line in raw.splitlines():
        m = _TS_RE.match(line.strip())
        if m:
            is_synced = True
            mins, secs = int(m.group(1)), int(m.group(2))
            frac = m.group(3) or "0"
            # Normalise fraction to milliseconds (handles 2- or 3-digit fracs)
            ms = (mins * 60 + secs) * 1000 + int(frac.ljust(3, "0")[:3])
            text = m.group(4).strip()
            lines.append((ms, text))
        else:
            lines.append((0, line.strip()))

    if not lines:
        return False, []

    # If mixed (some timed, some not), treat as plain
    if is_synced:
        lines.sort(key=lambda x: x[0])

    return is_synced, lines


def _active_index(lines: List[Tuple[int, str]], position_ms: int) -> int:
    """Return the index of the line that should be shown at position_ms."""
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts <= position_ms:
            idx = i
        else:
            break
    return idx


# ──────────────────────────────────────────────────────────────────────────────
#  Faded scroll area (for plain lyrics)
# ──────────────────────────────────────────────────────────────────────────────


class _FadedScrollArea(QScrollArea):
    """Scroll area — just a clean wrapper for plain lyrics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 4px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(133,153,234,0.30);"
            " border-radius: 2px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height: 0px; }"
        )
        self.setWidgetResizable(True)


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

_TOGGLE_ACTIVE = """
    QPushButton {
        background: rgba(133, 153, 234, 0.22);
        border: 1px solid rgba(133, 153, 234, 0.50);
        border-radius: 8px;
        color: rgba(230, 235, 255, 0.90);
        font-size: 9px;
        font-weight: bold;
        letter-spacing: 1px;
        padding: 2px 8px;
    }
"""

_TOGGLE_INACTIVE = """
    QPushButton {
        background: transparent;
        border: 1px solid rgba(133, 153, 234, 0.22);
        border-radius: 8px;
        color: rgba(133, 153, 234, 0.45);
        font-size: 9px;
        font-weight: bold;
        letter-spacing: 1px;
        padding: 2px 8px;
    }
    QPushButton:hover {
        background: rgba(133, 153, 234, 0.10);
        color: rgba(200, 208, 244, 0.70);
    }
"""


# ──────────────────────────────────────────────────────────────────────────────
#  Marquee (panning) label — used for artist names that may be very long
# ──────────────────────────────────────────────────────────────────────────────


class _MarqueeLabel(QWidget):
    """
    A single-line label that scrolls (pans) its text horizontally when the
    text is wider than the widget.  No album-art space is consumed.
    """

    _SCROLL_STEP_PX = 1  # pixels per tick
    _SCROLL_INTERVAL_MS = 30  # ~33 fps
    _PAUSE_TICKS = 60  # ticks to pause at each end (~1.8 s)
    _FADE_WIDTH = 18  # px of fade-out at edges when scrolling

    def __init__(self, text: str, font: QFont, color: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._font = font
        self._color = color
        self._offset = 0  # current horizontal scroll offset
        self._direction = 1  # 1 = scrolling right-to-left, -1 = back
        self._pause_remaining = self._PAUSE_TICKS
        self._text_width = 0

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        self._timer = QTimer(self)
        self._timer.setInterval(self._SCROLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def set_text(self, text: str):
        self._text = text
        self._offset = 0
        self._direction = 1
        self._pause_remaining = self._PAUSE_TICKS
        self._timer.stop()
        self.update()
        # Defer scroll check until after first paint gives us real geometry
        QTimer.singleShot(200, self._check_scroll_needed)

    def _check_scroll_needed(self):
        fm = self.fontMetrics()
        self._text_width = fm.horizontalAdvance(self._text)
        if self._text_width > self.width():
            self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._check_scroll_needed()

    def _tick(self):
        if self._pause_remaining > 0:
            self._pause_remaining -= 1
            return
        max_offset = self._text_width - self.width() + self._FADE_WIDTH
        self._offset += self._SCROLL_STEP_PX * self._direction
        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
            self._pause_remaining = self._PAUSE_TICKS
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause_remaining = self._PAUSE_TICKS
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self._font)

        # Parse colour string into QColor
        c = QColor(self._color) if not self._color.startswith("rgba") else None
        if c is None:
            # Handle rgba(r,g,b,a) where a is 0–1
            nums = [
                x.strip() for x in self._color.lstrip("rgba(").rstrip(")").split(",")
            ]
            try:
                r, g, b = int(nums[0]), int(nums[1]), int(nums[2])
                a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
            except Exception:
                r, g, b, a = 180, 190, 240, 178
            c = QColor(r, g, b, a)

        painter.setPen(c)
        painter.drawText(
            QRect(-self._offset, 0, self._text_width + 4, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )

        # Fade edges when scrolling
        if self._text_width > self.width():
            w = self.width()
            h = self.height()
            bg = QColor(0, 0, 0, 0)  # transparent
            for x, fade_right in ((0, False), (w - self._FADE_WIDTH, True)):
                grad = QLinearGradient(
                    QPoint(x, 0),
                    QPoint(x + self._FADE_WIDTH * (1 if fade_right else -1), 0),
                )
                grad.setColorAt(0.0, QColor(0, 0, 0, 200))
                grad.setColorAt(1.0, bg)
                painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
                painter.fillRect(
                    x if fade_right else x - self._FADE_WIDTH,
                    0,
                    self._FADE_WIDTH,
                    h,
                    grad,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        painter.end()

    def fontMetrics(self):
        return QFontMetrics(self._font)


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
        self._show_all_lyrics = False  # Toggle: karaoke vs full plain view
        self._lyrics_lines: List[Tuple[int, str]] = []
        self._active_idx = -1
        self._last_position_ms = -1

        # Load saved offset from config (stored as tenths of a second, int)
        self._saved_offset_tenths = app_config.get_lyrics_sync_offset()
        self._sync_offset_ms = self._saved_offset_tenths * 100

        # Debounce timer for saving offset to config
        self._offset_save_timer = QTimer(self)
        self._offset_save_timer.setSingleShot(True)
        self._offset_save_timer.setInterval(_OFFSET_DEBOUNCE_MS)
        self._offset_save_timer.timeout.connect(self._save_offset_to_config)

        # Countdown timer for "lyrics coming soon" display
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(500)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._next_lyric_ms: int = -1

        # Cinema mode state
        self._cinema_mode = False

        # Art slideshow state
        self._art_images: List[QPixmap] = []
        self._art_slide_idx: int = 0
        self._art_slide_timer = QTimer(self)
        self._art_slide_timer.setInterval(6_000)  # 6 s per image
        self._art_slide_timer.timeout.connect(self._advance_art_slide)

        self._initUI()
        self._setup_cinema_shortcut()

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

    # ── cinema mode ──────────────────────────────────────────────────────

    def _setup_cinema_shortcut(self):
        """Register Ctrl+Shift+F to toggle cinema (immersive) mode."""
        self._cinema_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        self._cinema_shortcut.setContext(Qt.ApplicationShortcut)
        self._cinema_shortcut.activated.connect(self.toggle_cinema_mode)

    def toggle_cinema_mode(self):
        """Hide/show player dock, navigation dock, and menu bar."""
        self._cinema_mode = not self._cinema_mode
        try:
            main_win = self.window()
            if self._cinema_mode:
                # Hide menu bar
                mb = getattr(main_win, "menuBar", lambda: None)()
                if mb:
                    mb.setVisible(False)
                # Hide docks
                for attr in ("player_dock", "navigation_dock", "queue_dock"):
                    dock = getattr(main_win, attr, None)
                    if dock:
                        dock.setVisible(False)
            else:
                # Re-fetch widgets fresh — stored references go stale after
                # track changes, which caused docks/menu bar to stay hidden.
                mb = getattr(main_win, "menuBar", lambda: None)()
                if mb:
                    mb.setVisible(True)
                for attr in ("player_dock", "navigation_dock", "queue_dock"):
                    dock = getattr(main_win, attr, None)
                    if dock:
                        dock.setVisible(True)
        except Exception as exc:
            logger.warning(f"toggle_cinema_mode: {exc}")

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
        left_layout.addWidget(self._art_card, stretch=1)

        root.addWidget(left_widget, 42)

        # ── RIGHT — metadata + content ───────────────────────────────────
        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(16, 36, 32, 24)
        right_layout.setSpacing(4)

        # Title
        self._title_lbl = QLabel("No Track Playing")
        self._title_lbl.setFont(self._TITLE_FONT)
        self._title_lbl.setStyleSheet(
            "color: rgba(230,235,255,0.94); background: transparent; border: none;"
        )
        self._title_lbl.setWordWrap(True)
        right_layout.addWidget(self._title_lbl)

        # Artist — scrolling marquee so long names pan rather than truncate
        self._artist_marquee = _MarqueeLabel(
            "—", self._ARTIST_FONT, "rgba(180,190,240,0.70)"
        )
        self._artist_marquee.setFixedHeight(28)
        right_layout.addWidget(self._artist_marquee)

        # Album
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setStyleSheet(
            "color: rgba(150,160,210,0.50); background: transparent; border: none;"
        )
        right_layout.addWidget(self._album_lbl)

        right_layout.addSpacing(10)

        # Chips
        self._chip_duration = _Chip("⏱", "—")
        self._chip_bpm = _Chip("♩", "—")
        self._chip_key = _Chip("key", "—")
        self._chip_timesig = _Chip("𝄴", "—")
        self._chip_rec_year = _Chip("📅", "—")
        self._chip_plays = _Chip("▶", "—")
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

        # Toggle to show/hide the sync-offset slider row
        self._sync_toggle_btn = QPushButton("⏱")
        self._sync_toggle_btn.setFixedSize(24, 24)
        self._sync_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._sync_toggle_btn.setToolTip("Toggle lyric sync slider")
        self._sync_toggle_btn.setStyleSheet(_TOGGLE_INACTIVE)
        self._sync_toggle_btn.clicked.connect(self._on_toggle_sync_slider)
        tab_bar.addWidget(self._sync_toggle_btn)

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

        # Next lyric line — shown dimmer below the current karaoke line
        self._next_lyric_lbl = QLabel("")
        self._next_lyric_lbl.setFont(QFont("Cambria", 14, QFont.Normal))
        self._next_lyric_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.38); font-style: italic;"
            " background: transparent; border: none;"
        )
        self._next_lyric_lbl.setAlignment(Qt.AlignCenter)
        self._next_lyric_lbl.setWordWrap(True)
        self._next_lyric_lbl.setVisible(False)

        self._plain_area = _FadedScrollArea()
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

        # Countdown label (shown below current lyric when next line is ≥5 s away)
        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setAlignment(Qt.AlignCenter)
        self._countdown_lbl.setStyleSheet(
            "color: rgba(133,153,234,0.55); font-size: 13px; font-style: italic;"
            " background: transparent; border: none;"
        )
        self._countdown_lbl.setFixedHeight(28)
        self._countdown_lbl.setVisible(False)

        lp.addWidget(self._karaoke_lbl, stretch=1)
        lp.addWidget(self._next_lyric_lbl)  # fixed below karaoke line
        lp.addWidget(self._plain_area, stretch=1)
        lp.addWidget(self._no_lyrics_lbl, stretch=1)
        lp.addWidget(self._countdown_lbl)  # fixed height — sits below lyric

        # Sync offset row — contains slider + "SHOW ALL" toggle
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
        # Restore saved slider position
        self._offset_slider.setValue(self._saved_offset_tenths)
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

        # "SHOW ALL" / "KARAOKE" toggle button
        self._toggle_mode_btn = QPushButton("SHOW ALL")
        self._toggle_mode_btn.setFixedHeight(20)
        self._toggle_mode_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_mode_btn.setStyleSheet(_TOGGLE_INACTIVE)
        self._toggle_mode_btn.clicked.connect(self._on_toggle_lyrics_mode)

        off_lay.addWidget(self._offset_lbl)
        off_lay.addWidget(self._offset_slider)
        off_lay.addWidget(self._toggle_mode_btn)
        self._offset_row.setVisible(False)
        lp.addWidget(self._offset_row)

        # Page 1: CREDITS
        self._credits_panel = _CreditsPanel()

        self._stack.addWidget(lyrics_page)
        self._stack.addWidget(self._credits_panel)

        right_layout.addWidget(self._stack, stretch=1)

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

    # ── lyrics mode toggle ─────────────────────────────────────────────────

    def _on_toggle_lyrics_mode(self):
        """Switch between karaoke (synced) and full plain text view."""
        self._show_all_lyrics = not self._show_all_lyrics
        if self._show_all_lyrics:
            self._toggle_mode_btn.setText("KARAOKE")
            self._toggle_mode_btn.setStyleSheet(_TOGGLE_ACTIVE)
            # Show full plain text from the synced lines
            text = "\n".join(t for _, t in self._lyrics_lines)
            self._karaoke_lbl.setVisible(False)
            self._next_lyric_lbl.setVisible(False)
            self._countdown_lbl.setVisible(False)
            self._countdown_timer.stop()
            self._plain_lbl.setText(text)
            self._plain_area.setVisible(True)
            self._plain_area.verticalScrollBar().setValue(0)
        else:
            self._toggle_mode_btn.setText("SHOW ALL")
            self._toggle_mode_btn.setStyleSheet(_TOGGLE_INACTIVE)
            self._plain_area.setVisible(False)
            self._karaoke_lbl.setVisible(True)
            # Re-trigger display at current position
            self._last_position_ms = -1

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

            # Use primary_artist_names property (Oxford-comma formatted)
            artist_str = getattr(track, "primary_artist_names", None)
            if not artist_str:
                # Fallback: first artist in artists proxy
                artists = getattr(track, "artists", None) or []
                artist_str = getattr(artists[0], "artist_name", "") if artists else ""
            self._artist_marquee.set_text(artist_str or "—")

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

            self._load_art_from_track(track)

            logger.debug(f"updateUI TOTAL: {time.time() - t0:.3f}s")

        except Exception as exc:
            logger.error(
                f"NowPlayingView.updateUI failed: {exc}\n{traceback.format_exc()}"
            )
            self.clearUI()

    def clearUI(self):
        self.track = None
        self._title_lbl.setText("No Track Playing")
        self._artist_marquee.set_text("—")
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

        # Reset state
        self._is_synced = False
        self._show_all_lyrics = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._countdown_timer.stop()
        self._next_lyric_ms = -1

        if not raw or not raw.strip():
            self._set_lyrics_mode_none()
            return

        is_synced, lines = _parse_lyrics(raw)
        self._lyrics_lines = lines
        self._is_synced = is_synced

        if is_synced:
            self._set_lyrics_mode_karaoke()
            # Don't blindly show first line — let position sync handle it.
            # (Handles the case where lyrics start 5 min in.)
        else:
            self._set_lyrics_mode_plain("\n".join(t for _, t in lines))

    def _set_lyrics_mode_none(self):
        """No lyrics available — switch to Credits tab automatically."""
        self._is_synced = False
        self._lyrics_lines = []
        self._active_idx = -1
        self._countdown_timer.stop()
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._next_lyric_lbl.setVisible(False)
        self._plain_area.setVisible(False)
        self._plain_lbl.setText("")
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._offset_row.setVisible(False)
        # Auto-switch to Credits
        self._switch_tab(self._PAGE_CREDITS)

    def _set_lyrics_mode_karaoke(self):
        self._plain_area.setVisible(False)
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._next_lyric_lbl.setVisible(False)
        self._karaoke_lbl.setVisible(True)
        # Restore saved slider value (already set in __init__, keep it)
        # Slider row stays hidden until user clicks the ⏱ toggle
        # Reset toggle button label
        self._toggle_mode_btn.setText("SHOW ALL")
        self._toggle_mode_btn.setStyleSheet(_TOGGLE_INACTIVE)
        # Switch to lyrics tab
        self._switch_tab(self._PAGE_LYRICS)

    def _set_lyrics_mode_plain(self, text: str):
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._no_lyrics_lbl.setVisible(False)
        self._countdown_lbl.setVisible(False)
        self._offset_row.setVisible(False)
        self._plain_lbl.setText(text)
        self._plain_area.setVisible(True)
        self._plain_area.verticalScrollBar().setValue(0)
        # Switch to lyrics tab
        self._switch_tab(self._PAGE_LYRICS)

    # ── position sync ─────────────────────────────────────────────────────

    def _on_position_changed(self, position_ms: int):
        if not self._is_synced or not self._lyrics_lines:
            return
        # Skip if we're in "show all" mode — no karaoke tracking needed
        if self._show_all_lyrics:
            return
        if abs(position_ms - self._last_position_ms) < 150:
            return
        self._last_position_ms = position_ms

        effective_ms = position_ms + self._sync_offset_ms

        # Find which line is current and what the next line's timestamp is
        new_idx = _active_index(self._lyrics_lines, effective_ms)

        # Check gap to next upcoming lyric
        next_ts = self._find_next_lyric_ts(effective_ms)
        gap_ms = next_ts - effective_ms if next_ts >= 0 else -1

        # If we haven't reached the first lyric yet and it's far away → countdown
        if new_idx == 0 and self._lyrics_lines[0][0] > effective_ms:
            gap_to_first = self._lyrics_lines[0][0] - effective_ms
            if gap_to_first >= _LYRIC_GAP_THRESHOLD_MS:
                self._start_countdown(self._lyrics_lines[0][0])
                return

        # If current line is showing but next is far away → countdown after showing
        if new_idx == self._active_idx and gap_ms >= _LYRIC_GAP_THRESHOLD_MS:
            self._start_countdown(next_ts)
            return

        # Normal lyric display
        if new_idx != self._active_idx:
            self._stop_countdown()
            self._active_idx = new_idx
            text = self._lyrics_lines[new_idx][1]
            if text.strip():
                self._karaoke_lbl.show_line(text)
            else:
                # Blank line — check if next lyric is far
                if gap_ms >= _LYRIC_GAP_THRESHOLD_MS and next_ts >= 0:
                    self._start_countdown(next_ts)

            # Update next-line preview
            self._update_next_lyric_lbl(new_idx)

    def _update_next_lyric_lbl(self, current_idx: int):
        """Show the next non-empty lyric line below the current karaoke line."""
        next_text = ""
        for i in range(current_idx + 1, len(self._lyrics_lines)):
            t = self._lyrics_lines[i][1].strip()
            if t:
                next_text = t
                break
        if next_text:
            self._next_lyric_lbl.setText(next_text)
            self._next_lyric_lbl.setVisible(True)
        else:
            self._next_lyric_lbl.setText("")
            self._next_lyric_lbl.setVisible(False)

    def _on_toggle_sync_slider(self):
        """Show/hide the sync offset slider row."""
        visible = self._offset_row.isVisible()
        self._offset_row.setVisible(not visible)
        self._sync_toggle_btn.setStyleSheet(
            _TOGGLE_ACTIVE if not visible else _TOGGLE_INACTIVE
        )

    def _find_next_lyric_ts(self, effective_ms: int) -> int:
        """Return timestamp of the next lyric line after effective_ms, or -1."""
        for ts, text in self._lyrics_lines:
            if ts > effective_ms and text.strip():
                return ts
        return -1

    def _start_countdown(self, target_ms: int):
        """Show countdown to target_ms below the current lyric line.
        The karaoke label stays visible so the last line isn't clipped away —
        only the small countdown indicator is added beneath it."""
        self._next_lyric_ms = target_ms
        # Keep karaoke label showing — don't hide it
        self._karaoke_lbl.setVisible(True)
        self._countdown_lbl.setVisible(True)
        self._update_countdown()
        if not self._countdown_timer.isActive():
            self._countdown_timer.start()

    def _stop_countdown(self):
        self._countdown_timer.stop()
        self._countdown_lbl.setVisible(False)
        self._karaoke_lbl.setVisible(True)
        self._next_lyric_ms = -1

    def _update_countdown(self):
        """Refresh the countdown label text."""
        if self._next_lyric_ms < 0:
            self._countdown_timer.stop()
            return
        remaining_ms = self._next_lyric_ms - (
            self._last_position_ms + self._sync_offset_ms
        )
        if remaining_ms <= 0:
            self._stop_countdown()
            return
        secs = remaining_ms / 1000
        if secs >= 60:
            m, s = int(secs) // 60, int(secs) % 60
            txt = f"♪  in {m}:{s:02d}"
        else:
            txt = f"♪  in {secs:.0f}s"
        self._countdown_lbl.setText(txt)

    def _on_offset_changed(self, value: int):
        """Slider moved — update offset immediately, debounce the config save."""
        self._sync_offset_ms = value * 100
        secs = self._sync_offset_ms / 1000
        sign = "+" if secs >= 0 else "−"
        self._offset_lbl.setText(f"Sync  {sign}{abs(secs):.1f}s")
        self._last_position_ms = -1
        # Restart debounce timer
        self._offset_save_timer.start()

    def _save_offset_to_config(self):
        """Persist the current offset value to config."""
        try:
            app_config.set_lyrics_sync_offset(self._offset_slider.value())
            app_config.save()
            logger.debug(f"Saved lyrics sync offset: {self._offset_slider.value()}")
        except Exception as exc:
            logger.warning(f"Could not save lyrics sync offset: {exc}")

    # ── chips ─────────────────────────────────────────────────────────────

    def _update_chips(self, track):
        visible: List[_Chip] = []

        def _maybe(chip: _Chip, val):
            """Add chip if val is a non-empty string."""
            if val is not None and str(val).strip():
                chip.set_value(str(val))
                visible.append(chip)

        def _safe(chip: _Chip, fn):
            """Run fn() to get a formatted string; silently skip this chip on any error.
            This means one missing/broken field never prevents others from showing."""
            try:
                val = fn()
                _maybe(chip, val)
            except Exception as exc:
                logger.debug(f"_update_chips: skipping chip due to error: {exc}")

        # ── Basic metadata ─────────────────────────────────────────────────
        _safe(
            self._chip_bpm,
            lambda: (
                f"{float(track.bpm):.0f} BPM"
                if getattr(track, "bpm", None) is not None
                else None
            ),
        )
        _safe(
            self._chip_key,
            lambda: (
                f"{track.key} {(getattr(track, 'mode', '') or '')}".strip()
                if getattr(track, "key", None)
                else None
            ),
        )
        _safe(
            self._chip_timesig,
            lambda: (
                str(track.primary_time_signature)
                if getattr(track, "primary_time_signature", None) is not None
                else None
            ),
        )

        # ── User & library data ────────────────────────────────────────────
        _safe(
            self._chip_plays,
            lambda: (
                f"{int(track.play_count)} plays"
                if getattr(track, "play_count", None) is not None
                else None
            ),
        )
        _safe(
            self._chip_genres,
            lambda: (
                ", ".join(
                    n
                    for n in [
                        getattr(g, "genre_name", "")
                        for g in (getattr(track, "genres", None) or [])[:3]
                    ]
                    if n
                )
                or None
            ),
        )

        self._chip_row.set_chips(visible)

    # ── art ───────────────────────────────────────────────────────────────

    def _load_art(self, pixmap: Optional[QPixmap]):
        """Single-image path kept for clearUI / fallback use."""
        self._start_art_slideshow([pixmap] if pixmap else [])

    def _load_art_from_track(self, track):
        """Build slideshow from all available art images for this track."""
        album = getattr(track, "album", None)
        paths: List[Optional[str]] = []
        if album:
            for attr in (
                "front_cover_path",
                "back_cover_path",
                "liner_path",
                "artist_image_path",
            ):
                p = getattr(album, attr, None) or ""
                if p:
                    paths.append(p)
        # Also try artist-level image
        for artist in getattr(track, "artists", None) or []:
            p = getattr(artist, "image_path", None) or ""
            if p and p not in paths:
                paths.append(p)

        pixmaps: List[QPixmap] = []
        for p in paths:
            if Path(p).exists():
                px = QPixmap(str(p))
                if not px.isNull():
                    pixmaps.append(px)

        if not pixmaps:
            default = self.default_art_path
            if default and Path(default).exists():
                px = QPixmap(default)
                if not px.isNull():
                    pixmaps.append(px)

        self._start_art_slideshow(pixmaps)

    def _start_art_slideshow(self, pixmaps: List[QPixmap]):
        """Begin cycling through the given list of pixmaps."""
        self._art_slide_timer.stop()
        self._art_images = pixmaps
        self._art_slide_idx = 0

        first = pixmaps[0] if pixmaps else None
        self._apply_art(first)

        if len(pixmaps) > 1:
            self._art_slide_timer.start()

    def _advance_art_slide(self):
        if not self._art_images:
            return
        self._art_slide_idx = (self._art_slide_idx + 1) % len(self._art_images)
        self._apply_art(self._art_images[self._art_slide_idx])

    def _apply_art(self, pixmap: Optional[QPixmap]):
        """Push a single pixmap to the art card and backdrop with a fade."""
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
