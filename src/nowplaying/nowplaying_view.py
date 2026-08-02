"""
NowPlayingView module — Cinematic redesign.
"""

import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.common.style_utils import set_style_property
from src.core.asset_paths import asset
from src.core.censor import censor_text
from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.album.album_art_worker import ArtCacheWorker
from src.image.artwork_cache import get_artwork_cache
from src.nowplaying.nowplaying_art import _ArtCard
from src.nowplaying.nowplaying_backdrop import _BlurredBackdrop
from src.nowplaying.nowplaying_chip import _Chip, _ScrollingChipRow
from src.nowplaying.nowplaying_credits import _CreditsPanel
from src.nowplaying.nowplaying_karaoke import _KaraokeLine
from src.nowplaying.nowplaying_lyrics_parser import active_index, parse_lyrics
from src.nowplaying.nowplaying_marquee import FadedScrollArea, MarqueeLabel

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

# If the next lyric line starts more than this many ms in the future, show a
# countdown timer instead of a blank karaoke display.
_LYRIC_GAP_THRESHOLD_MS = 5_000

# Debounce delay (ms) before persisting the sync-offset slider to config.
_OFFSET_DEBOUNCE_MS = 600

# Art slideshow dwell times. Album covers get more screen time than artist
# photos so the cover art still dominates the rotation. Both are long enough
# to actually read a credit line / take in a photo, not just flash by.
_COVER_DWELL_MS = 6_000
_ARTIST_DWELL_MS = 5_000

# When a front cover is present, it's interleaved between every other image
# (front, rear, front, liner, front, artist-photo, ...) rather than shown as
# one single slide, so it never gets rotated away for long and doesn't turn
# into one giant multi-minute freeze on a track with many contributors. Each
# time it's shown, its dwell is set relative to the *next* image's dwell so
# the front cover ends up with this fraction of total cycle time no matter
# how many secondary images are in the rotation.
_FRONT_COVER_SHARE = 0.8

# Duration of the crossfade between successive art-slideshow images.
_ART_TRANSITION_MS = 950

# Tab and toggle button visuals live in themes/dark_mode.qss under the
# [npTab="true"] / [npToggle="true"] / [active=...] selectors — see _set_active().


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
        self._art_transition_anim: Optional[QPropertyAnimation] = None

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
        self._art_images: List[Tuple[QPixmap, bool, Optional[str]]] = []
        self._art_has_front: bool = False
        self._art_slide_idx: int = 0
        self._art_slide_timer = QTimer(self)
        self._art_slide_timer.setInterval(_COVER_DWELL_MS)
        self._art_slide_timer.timeout.connect(self._advance_art_slide)

        # Background art-cache warming (avoids blocking the UI thread on a
        # cold cache / audio-file decode - see _load_art_from_track)
        self._art_worker: Optional[ArtCacheWorker] = None
        self._art_generation = 0

        self._initUI()
        self._setup_cinema_shortcut()

        try:
            self.controller.mediaplayer.position_changed.connect(
                self._on_position_changed
            )
        except (AttributeError, RuntimeError) as exc:
            logger.warning(f"NowPlayingView: could not connect position_changed: {exc}")

        if self.track:
            self.updateUI(self.track)
        else:
            self.clearUI()

    # ── cinema mode ──────────────────────────────────────────────────────

    @property
    def cinema_mode(self) -> bool:
        return self._cinema_mode

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
            mb = getattr(main_win, "menuBar", lambda: None)()
            if self._cinema_mode:
                if mb:
                    mb.setVisible(False)
                # Remember whether the queue was actually open so exiting
                # cinema mode doesn't force it open regardless of prior state.
                queue_dock = getattr(main_win, "queue_dock", None)
                self._pre_cinema_queue_visible = (
                    queue_dock.isVisible() if queue_dock else False
                )
                for attr in ("player_dock", "navigation_dock", "queue_dock"):
                    dock = getattr(main_win, attr, None)
                    if dock:
                        dock.setVisible(False)
            else:
                # Re-fetch widgets fresh — stored references go stale after
                # track changes, which caused docks/menu bar to stay hidden.
                if mb:
                    mb.setVisible(True)
                for attr in ("player_dock", "navigation_dock"):
                    dock = getattr(main_win, attr, None)
                    if dock:
                        dock.setVisible(True)
                if hasattr(main_win, "set_queue_visible"):
                    main_win.set_queue_visible(
                        getattr(self, "_pre_cinema_queue_visible", False)
                    )
        except RuntimeError as exc:
            logger.warning(f"toggle_cinema_mode: {exc}")

    # ── build UI ──────────────────────────────────────────────────────────

    def _initUI(self):
        self.setMinimumSize(760, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setProperty("bgTransparent", True)

        self._backdrop = _BlurredBackdrop(self)
        self._backdrop.lower()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT — album art ─────────────────────────────────────────────
        left_widget = QWidget()
        left_widget.setProperty("bgTransparent", True)
        left_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_widget.setMinimumWidth(260)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(32, 36, 16, 36)

        self._art_card = _ArtCard(backdrop=self._backdrop)
        self._art_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout.addWidget(self._art_card, stretch=1)

        root.addWidget(left_widget, 42)

        # ── RIGHT — metadata + content ───────────────────────────────────
        right_widget = QWidget()
        right_widget.setProperty("bgTransparent", True)
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(16, 36, 32, 24)
        right_layout.setSpacing(4)

        # Title
        self._title_lbl = QLabel("No Track Playing")
        self._title_lbl.setFont(self._TITLE_FONT)
        self._title_lbl.setProperty("npRole", "title")
        self._title_lbl.setWordWrap(True)
        right_layout.addWidget(self._title_lbl)

        # Artist — scrolling marquee so long names pan rather than truncate
        self._artist_marquee = MarqueeLabel(
            "—", self._ARTIST_FONT, "rgba(180,190,240,0.70)"
        )
        # Match the title/album labels' natural line height instead of a
        # hardcoded value, so vertical spacing between the three lines is even.
        self._artist_marquee.setFixedHeight(QFontMetrics(self._ARTIST_FONT).height())
        right_layout.addWidget(self._artist_marquee)

        # Album
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setProperty("npRole", "album")
        self._album_lbl.setWordWrap(True)
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
            btn.setProperty("npTab", True)

        self._set_active(self._tab_lyrics, True)
        self._set_active(self._tab_credits, False)

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
        self._sync_toggle_btn.setProperty("npToggle", True)
        self._set_active(self._sync_toggle_btn, False)
        self._sync_toggle_btn.clicked.connect(self._on_toggle_sync_slider)
        tab_bar.addWidget(self._sync_toggle_btn)

        right_layout.addLayout(tab_bar)

        tab_rule = QFrame()
        tab_rule.setFrameShape(QFrame.HLine)
        tab_rule.setProperty("npRule", True)
        tab_rule.setFixedHeight(1)
        right_layout.addWidget(tab_rule)
        right_layout.addSpacing(10)

        # ── Stacked pages ─────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setProperty("bgTransparent", True)
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Page 0: LYRICS
        lyrics_page = QWidget()
        lyrics_page.setProperty("bgTransparent", True)
        lp = QVBoxLayout(lyrics_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)

        self._karaoke_lbl = _KaraokeLine()
        self._karaoke_lbl.setVisible(False)

        # Next lyric line — shown dimmer below the current karaoke line
        self._next_lyric_lbl = QLabel("")
        self._next_lyric_lbl.setFont(QFont("Cambria", 14, QFont.Normal))
        self._next_lyric_lbl.setProperty("npRole", "nextLyric")
        self._next_lyric_lbl.setAlignment(Qt.AlignCenter)
        self._next_lyric_lbl.setWordWrap(True)
        self._next_lyric_lbl.setVisible(False)

        self._plain_area = FadedScrollArea()
        self._plain_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._plain_lbl = QLabel()
        self._plain_lbl.setFont(self._PLAIN_FONT)
        self._plain_lbl.setProperty("npRole", "plainLyrics")
        self._plain_lbl.setWordWrap(True)
        self._plain_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._plain_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._plain_lbl.setContentsMargins(0, 8, 0, 24)
        self._plain_area.setWidget(self._plain_lbl)
        self._plain_area.setVisible(False)

        self._no_lyrics_lbl = QLabel("No lyrics available")
        self._no_lyrics_lbl.setAlignment(Qt.AlignCenter)
        self._no_lyrics_lbl.setProperty("npRole", "noLyrics")
        self._no_lyrics_lbl.setVisible(False)

        # Countdown label (shown below current lyric when next line is ≥5 s away)
        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setAlignment(Qt.AlignCenter)
        self._countdown_lbl.setProperty("npRole", "countdown")
        self._countdown_lbl.setFixedHeight(28)
        self._countdown_lbl.setVisible(False)

        # Current + next lyric are grouped so they stay close together;
        # a stretch inside the group (not the karaoke label itself) absorbs
        # the leftover vertical space instead of ballooning the gap between them.
        self._karaoke_block = QWidget()
        self._karaoke_block.setProperty("bgTransparent", True)
        kb = QVBoxLayout(self._karaoke_block)
        kb.setContentsMargins(0, 0, 0, 0)
        kb.setSpacing(6)
        kb.addWidget(self._karaoke_lbl)
        kb.addWidget(self._next_lyric_lbl)
        kb.addStretch(1)

        lp.addWidget(self._karaoke_block, stretch=1)
        lp.addWidget(self._plain_area, stretch=1)
        lp.addWidget(self._no_lyrics_lbl, stretch=1)
        lp.addWidget(self._countdown_lbl)  # fixed height — sits below lyric

        # Sync offset row — contains slider + "SHOW ALL" toggle
        self._offset_row = QWidget()
        self._offset_row.setProperty("bgTransparent", True)
        off_lay = QHBoxLayout(self._offset_row)
        off_lay.setContentsMargins(0, 6, 0, 0)
        off_lay.setSpacing(8)

        self._offset_lbl = QLabel("Sync  −0.5s")
        self._offset_lbl.setProperty("npRole", "offsetLabel")
        self._offset_lbl.setFixedWidth(80)

        self._offset_slider = QSlider(Qt.Horizontal)
        self._offset_slider.setObjectName("NowPlayingSyncSlider")
        self._offset_slider.setRange(-50, 50)
        # Restore saved slider position
        self._offset_slider.setValue(self._saved_offset_tenths)
        self._offset_slider.setTickInterval(5)
        self._offset_slider.setSingleStep(1)
        self._offset_slider.valueChanged.connect(self._on_offset_changed)

        # "SHOW ALL" / "KARAOKE" toggle button
        self._toggle_mode_btn = QPushButton("SHOW ALL")
        self._toggle_mode_btn.setFixedHeight(20)
        self._toggle_mode_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_mode_btn.setProperty("npToggle", True)
        self._set_active(self._toggle_mode_btn, False)
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

    @staticmethod
    def _set_active(button: QPushButton, active: bool) -> None:
        """Flip a [npTab]/[npToggle] button between its QSS active/inactive states."""
        set_style_property(button, "active", active)

    def _switch_tab(self, page: int):
        self._stack.setCurrentIndex(page)
        if page == self._PAGE_LYRICS:
            self._set_active(self._tab_lyrics, True)
            self._set_active(self._tab_credits, False)
            self._credits_panel.stop()
        else:
            self._set_active(self._tab_lyrics, False)
            self._set_active(self._tab_credits, True)
            self._credits_panel.load_credits(self.track)

    # ── lyrics mode toggle ─────────────────────────────────────────────────

    def _on_toggle_lyrics_mode(self):
        """Switch between karaoke (synced) and full plain text view."""
        self._show_all_lyrics = not self._show_all_lyrics
        if self._show_all_lyrics:
            self._toggle_mode_btn.setText("KARAOKE")
            self._set_active(self._toggle_mode_btn, True)
            # Show full plain text from the synced lines
            text = "\n".join(t for _, t in self._lyrics_lines)
            self._karaoke_lbl.setVisible(False)
            self._next_lyric_lbl.setVisible(False)
            self._karaoke_block.setVisible(False)
            self._countdown_lbl.setVisible(False)
            self._countdown_timer.stop()
            self._plain_lbl.setText(text)
            self._plain_area.setVisible(True)
            self._plain_area.verticalScrollBar().setValue(0)
        else:
            self._toggle_mode_btn.setText("SHOW ALL")
            self._set_active(self._toggle_mode_btn, False)
            self._plain_area.setVisible(False)
            self._karaoke_block.setVisible(True)
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
                censor_text(getattr(track, "track_name", None) or "Unknown Title")
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
                name = censor_text(getattr(album, "album_name", "") or "—")
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
            # Intentional broad boundary catch: dispatches to several
            # heterogeneous sub-updates (widget text, artist/album ORM
            # lookups, lyrics parsing, credits panel, art loading) on every
            # track change -- this is the single entry point for those and
            # must never leave the UI half-updated, so it falls back to
            # clearUI() instead of propagating.
            logger.error(
                f"NowPlayingView.updateUI failed: {exc}\n{traceback.format_exc()}"
            )
            self.clearUI()

    def clearUI(self):
        self._cancel_art_worker()
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
        raw = censor_text(getattr(track, "lyrics", None))

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

        is_synced, lines = parse_lyrics(raw)
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
        self._karaoke_block.setVisible(False)
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
        self._karaoke_block.setVisible(True)
        self._karaoke_lbl.setVisible(True)
        # Restore saved slider value (already set in __init__, keep it)
        # Slider row stays hidden until user clicks the ⏱ toggle
        # Reset toggle button label
        self._toggle_mode_btn.setText("SHOW ALL")
        self._set_active(self._toggle_mode_btn, False)
        # Switch to lyrics tab
        self._switch_tab(self._PAGE_LYRICS)

    def _set_lyrics_mode_plain(self, text: str):
        self._karaoke_lbl.setVisible(False)
        self._karaoke_lbl.clear_line()
        self._karaoke_block.setVisible(False)
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
        new_idx = active_index(self._lyrics_lines, effective_ms)

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
        self._set_active(self._sync_toggle_btn, not visible)

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
        except RuntimeError as exc:
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
                # Intentional broad boundary catch: fn is one of many
                # per-chip closures below with different failure modes
                # (float()/string parsing, attribute access) -- one broken
                # field must not prevent the other chips from showing (see
                # docstring).
                logger.debug(
                    f"_update_chips: skipping chip due to error: {exc}", exc_info=True
                )

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
        self._start_art_slideshow([(pixmap, False, None)] if pixmap else [])

    def _load_art_from_track(self, track):
        """Build slideshow from all available art images for this track."""
        self._cancel_art_worker()
        self._art_generation += 1
        gen = self._art_generation

        album = getattr(track, "album", None)
        is_explicit = bool(getattr(album, "art_is_explicit", False)) if album else False
        cache = get_artwork_cache()

        # (pixmap, is_artist_photo, label) — artist photos are rendered
        # without forcing a square crop, since they aren't necessarily
        # square, and carry the artist's name to caption the photo.
        pixmaps: List[Tuple[QPixmap, bool, Optional[str]]] = []
        has_front = False
        if album and cache:
            # peek_has_art never reads/decodes the audio file. If the cache
            # row is confirmed valid, front/rear/liner are all safe to fetch
            # synchronously (get_pixmap warms all three roles in one pass,
            # so a hit on "front" means the others are cache hits too). If
            # it's unknown (cold cache/stale mtime), resolve it on a
            # background thread instead of blocking the UI on a file decode.
            known = cache.peek_has_art(album, "front")
            if known is None:
                self._start_art_worker(album, gen, track)
            else:
                for role in ("front", "rear", "liner"):
                    px = cache.get_pixmap(album, role, is_explicit)
                    if not px.isNull():
                        pixmaps.append((px, False, None))
                        if role == "front":
                            has_front = True

        # Also try artist-level image
        album_artist_ids = {
            a.artist_id for a in (getattr(album, "album_artists", None) or [])
        }
        for artist in getattr(track, "artists", None) or []:
            p = getattr(artist, "profile_pic_path", None) or ""
            if p and Path(p).exists():
                px = QPixmap(str(p))
                if not px.isNull():
                    name = getattr(artist, "artist_name", None) or None
                    if name and artist.artist_id not in album_artist_ids:
                        credit_roles = []
                        for ar in getattr(track, "artist_roles", None) or []:
                            if ar.artist_id != artist.artist_id:
                                continue
                            role_name = getattr(ar.role, "role_name", None)
                            if role_name and role_name not in (
                                "Primary Artist",
                                "Album Artist",
                            ) and role_name not in credit_roles:
                                credit_roles.append(role_name)
                        if credit_roles:
                            name = f"{name} ({', '.join(credit_roles)})"
                    pixmaps.append((px, True, name))

        if not pixmaps:
            default = self.default_art_path
            if default and Path(default).exists():
                px = QPixmap(default)
                if not px.isNull():
                    pixmaps.append((px, False, None))

        self._start_art_slideshow(pixmaps, has_front=has_front)

    def _cancel_art_worker(self):
        if self._art_worker is not None:
            self._art_worker.request_cancel()
            self._art_worker.wait()
            self._art_worker = None

    def _start_art_worker(self, album, gen: int, track):
        cache = get_artwork_cache()
        self._art_worker = ArtCacheWorker([album], cache, "front")
        self._art_worker.resolved.connect(
            lambda _album_id, g=gen, t=track: self._on_art_resolved(g, t)
        )
        self._art_worker.start()

    def _on_art_resolved(self, gen: int, track):
        """Cache row for `track`'s album is now warm - re-run the (now
        synchronous/cheap) art lookup, but only if nothing has superseded
        this request in the meantime."""
        if gen != self._art_generation or track is not self.track:
            return
        self._load_art_from_track(track)

    def _start_art_slideshow(
        self,
        pixmaps: List[Tuple[QPixmap, bool, Optional[str]]],
        has_front: bool = False,
    ):
        """Begin cycling through the given list of (pixmap, is_artist, label)
        triples. The blurred backdrop stays pinned to the album art (the
        first non-artist image) for the whole track; only the small art
        card rotates through the full set, artist photos included.

        When a front cover is present, the rotation interleaves it between
        every other image (front, rear, front, liner, front, artist, ...)
        instead of visiting it once per lap - see _dwell_for_index.
        """
        self._art_slide_timer.stop()

        first = pixmaps[0] if pixmaps else (None, False, None)
        backdrop_pixmap = next((px for px, is_artist, _ in pixmaps if not is_artist), first[0])

        if has_front and len(pixmaps) > 1:
            front, secondaries = pixmaps[0], pixmaps[1:]
            sequence = []
            for img in secondaries:
                sequence.append(front)
                sequence.append(img)
        else:
            sequence = pixmaps

        self._art_images = sequence
        self._art_has_front = has_front
        self._art_slide_idx = 0

        self._apply_art(*first)
        self._apply_backdrop(backdrop_pixmap)

        if len(sequence) > 1:
            self._art_slide_timer.setInterval(self._dwell_for_index(0))
            self._art_slide_timer.start()

    def _advance_art_slide(self):
        if not self._art_images:
            return
        self._art_slide_idx = (self._art_slide_idx + 1) % len(self._art_images)
        current = self._art_images[self._art_slide_idx]
        self._apply_art(*current)
        self._art_slide_timer.setInterval(self._dwell_for_index(self._art_slide_idx))

    def _dwell_for_index(self, idx: int) -> int:
        """How long the image at `idx` should stay on screen.

        Normally each image just dwells for its per-type duration. When a
        front cover is in the rotation, it occupies every even slot
        (front/secondary/front/secondary/...) - each visit's dwell is set
        relative to the secondary image right after it, so the front cover
        gets _FRONT_COVER_SHARE of cycle time regardless of how many
        secondary images there are, without any single slide ballooning.
        """
        _, is_artist, _ = self._art_images[idx]
        base = _ARTIST_DWELL_MS if is_artist else _COVER_DWELL_MS
        if not self._art_has_front or len(self._art_images) <= 1:
            return base
        is_front_slot = idx % 2 == 0
        if not is_front_slot:
            return base
        next_idx = (idx + 1) % len(self._art_images)
        _, next_is_artist, _ = self._art_images[next_idx]
        neighbor = _ARTIST_DWELL_MS if next_is_artist else _COVER_DWELL_MS
        return round(neighbor * _FRONT_COVER_SHARE / (1 - _FRONT_COVER_SHARE))

    def _apply_art(
        self, pixmap: Optional[QPixmap], is_artist: bool = False, label: Optional[str] = None
    ):
        """Push a single pixmap to the small art card with a crossfade."""
        self._current_pixmap = pixmap
        self._art_card.set_art(pixmap, is_artist, label)

        if self._art_transition_anim:
            self._art_transition_anim.stop()

        self._art_transition_anim = QPropertyAnimation(self._art_card, b"transitionProgress")
        self._art_transition_anim.setDuration(_ART_TRANSITION_MS)
        self._art_transition_anim.setStartValue(0.0)
        self._art_transition_anim.setEndValue(1.0)
        self._art_transition_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._art_transition_anim.start()

    def _apply_backdrop(self, pixmap: Optional[QPixmap]):
        """Crossfade the full-window blurred backdrop to `pixmap`. Called
        once per track (or when a new backdrop candidate appears), not on
        every art-card slide."""
        if self._fade_anim:
            self._fade_anim.stop()

        self._backdrop.set_pixmap(pixmap)
        self._backdrop._opacity = 0.0

        self._fade_anim = QPropertyAnimation(self._backdrop, b"backdropOpacity")
        self._fade_anim.setDuration(_ART_TRANSITION_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade_anim.start()
