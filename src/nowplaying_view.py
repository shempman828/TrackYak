"""
NowPlayingView module — Cinematic redesign.
"""

import re
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QPixmap, QShortcut
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

        # Artist
        self._artist_lbl = QLabel("—")
        self._artist_lbl.setFont(self._ARTIST_FONT)
        self._artist_lbl.setStyleSheet(
            "color: rgba(180,190,240,0.70); background: transparent; border: none;"
        )
        right_layout.addWidget(self._artist_lbl)

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
        self._chip_track_no = _Chip("#", "—")
        self._chip_bpm = _Chip("♩", "—")
        self._chip_key = _Chip("♭", "—")
        self._chip_timesig = _Chip("𝄴", "—")
        self._chip_bitrate = _Chip("⚡", "—")
        self._chip_sample = _Chip("〜", "—")
        self._chip_depth = _Chip("◈", "—")
        self._chip_rec_year = _Chip("📅", "—")
        self._chip_plays = _Chip("▶", "—")
        self._chip_rating = _Chip("★", "—")
        self._chip_genres = _Chip("🎵", "—")
        # Advanced audio analysis chips
        self._chip_energy = _Chip("⚡", "—")
        self._chip_danceability = _Chip("🕺", "—")
        self._chip_valence = _Chip("☀", "—")
        self._chip_acousticness = _Chip("🎸", "—")
        self._chip_liveness = _Chip("🎤", "—")
        self._chip_fidelity = _Chip("◉", "—")
        self._chip_gain = _Chip("🔊", "—")

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
            self._artist_lbl.setText(artist_str or "—")

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
        self._karaoke_lbl.setVisible(True)
        # Restore saved slider value (already set in __init__, keep it)
        self._offset_row.setVisible(True)
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
