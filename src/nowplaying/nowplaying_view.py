"""NowPlayingView module — Cinematic redesign."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time
import traceback

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from src.album.album_art_worker import ArtCacheWorker
from src.common.widgets.style_utils import set_style_property
from src.foundation.asset_paths import asset
from src.foundation.censor import censor_text
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.nowplaying_about import _AboutPanel
from src.nowplaying.nowplaying_art import _ArtCard
from src.nowplaying.nowplaying_art_column import _ArtColumn, _SlideDots
from src.nowplaying.nowplaying_art_slideshow import _COVER_DWELL_MS, NowPlayingArtMixin
from src.nowplaying.nowplaying_backdrop import _BlurredBackdrop
from src.nowplaying.nowplaying_chip import _Chip, _ScrollingChipRow
from src.nowplaying.nowplaying_credits import _CreditsPanel
from src.nowplaying.nowplaying_lyric_column import _LyricColumn
from src.nowplaying.nowplaying_lyrics import NowPlayingLyricsMixin
from src.nowplaying.nowplaying_marquee import MarqueeLabel
from src.nowplaying.nowplaying_progress import _ProgressStrip

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

# Debounce delay (ms) before persisting the sync offset to config.
_OFFSET_DEBOUNCE_MS = 600

# Dwell time (ms) per tab when auto-cycle mode is running (Ctrl+Shift++).
_AUTO_CYCLE_DWELL_MS = 8000

# Margin (px) around the album art that its drop shadow paints into.
_ART_SHADOW_PAD = 24

# Tab and toggle button visuals live in themes/dark_mode.qss under the
# [npTab="true"] / [npToggle="true"] / [active=...] selectors — see _set_active().


@dataclass
class _TabSpec:
    """One entry in NowPlayingView's tab registry.

    ``_switch_tab`` / ``updateUI`` / ``clearUI`` iterate ``self._tabs`` instead
    of branching on page constants. List order is the stack/page index.

    - ``on_show(track)`` runs when the tab becomes visible and, for the visible
      tab, on every ``updateUI``; called with ``None`` from ``clearUI``.
    - ``on_hide()`` runs when another tab is selected.
    """

    key: str
    label: str
    widget: QWidget
    button: QPushButton | None = None
    on_show: Callable[[object], None] | None = None
    on_hide: Callable[[], None] | None = None


# ──────────────────────────────────────────────────────────────────────────────
#  Title line
# ──────────────────────────────────────────────────────────────────────────────


class _AdaptiveTitle(QWidget):
    """Track-title line that word-wraps like a normal label and only falls back
    to a horizontally-panning :class:`MarqueeLabel` when the wrapped title would
    need more than ``_MAX_LINES`` lines.

    ``set_text`` is the single update path. The choice is re-evaluated on every
    resize because the column width drives the wrapped line count; the first
    evaluation is retried briefly until real geometry is available (same
    deferred-geometry problem ``MarqueeLabel`` solves with a singleShot).
    """

    _MAX_LINES = 3
    _MAX_RETRIES = 10

    def __init__(self, text: str, font: QFont, color: str, parent=None):
        super().__init__(parent)
        self._font = font
        self._text = text
        self._retries = 0
        self.setProperty("bgTransparent", True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._wrap = QLabel(text)
        self._wrap.setFont(font)
        self._wrap.setWordWrap(True)
        self._wrap.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._wrap.setStyleSheet(f"color: {color}; background: transparent;")
        self._wrap.setProperty("bgTransparent", True)
        lay.addWidget(self._wrap)

        # Off-screen twin used only to measure wrapped height. ``_wrap`` itself
        # can't be measured: ``_apply_layout`` pins it with ``setFixedHeight``
        # and ``QLabel.heightForWidth`` clamps to the widget's max height, so
        # measuring ``_wrap`` just reads back the previous title's pinned height
        # and the title could only ever grow, never shrink.
        self._probe = QLabel()
        self._probe.setFont(font)
        self._probe.setWordWrap(True)
        self._probe.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._probe.hide()

        self._marquee = MarqueeLabel(text, font, color)
        self._marquee.setFixedHeight(QFontMetrics(font).height())
        self._marquee.hide()
        lay.addWidget(self._marquee)

        self._apply_layout()

    # ── public API ────────────────────────────────────────────────────────
    def set_text(self, text: str):
        self._text = text
        self._retries = 0
        self._apply_layout()

    # ── internals ─────────────────────────────────────────────────────────
    def _avail_width(self) -> int:
        w = self.contentsRect().width()
        if w <= 0:
            w = self._wrap.contentsRect().width()
        return w

    def _line_count(self, text: str, width: int) -> int:
        if width <= 0:
            return 1
        # Measure with a QLabel configured exactly like the one that paints the
        # title. Its QTextLayout-based word-wrap can pick a different break point
        # than QFontMetrics.boundingRect's predictor, and whenever the predictor
        # came out one line pessimistic ``_apply_layout`` reserved a blank
        # trailing row. ``heightForWidth`` is the real render path, so it cannot
        # disagree with what gets drawn -- but it has to be read off an
        # unconstrained label (see ``self._probe``), never off ``_wrap`` which
        # ``_apply_layout`` pins with ``setFixedHeight``.
        self._probe.setText(text)
        h = self._probe.heightForWidth(width)
        if h <= 0:
            return 1
        return max(1, round(h / QFontMetrics(self._font).lineSpacing()))

    def _apply_layout(self):
        width = self._avail_width()
        if width <= 0 and self._retries < self._MAX_RETRIES:
            self._retries += 1
            QTimer.singleShot(50, self._apply_layout)
            return

        lines = self._line_count(self._text, width)
        if lines > self._MAX_LINES:
            self._wrap.hide()
            self._marquee.show()
            self._marquee.set_text(self._text)
        else:
            self._marquee.hide()
            self._wrap.setText(self._text)
            self._wrap.setFixedHeight(lines * QFontMetrics(self._font).lineSpacing())
            self._wrap.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_layout()


# ──────────────────────────────────────────────────────────────────────────────
#  Main view
# ──────────────────────────────────────────────────────────────────────────────


class NowPlayingView(NowPlayingLyricsMixin, NowPlayingArtMixin, QWidget):
    """Cinematic now-playing view with blurred backdrop and rich metadata.

    Lyrics/karaoke sync lives in NowPlayingLyricsMixin (nowplaying_lyrics.py).
    Album-art/backdrop slideshow lives in NowPlayingArtMixin
    (nowplaying_art_slideshow.py). This class owns UI construction, cinema
    mode, chips, and the public updateUI/clearUI entry points, and composes
    the other two.
    """

    _TITLE_FONT = QFont("Georgia", 28, QFont.Bold)
    _ARTIST_FONT = QFont("Cambria", 16, QFont.Normal)
    _ALBUM_FONT = QFont("Cambria", 13, QFont.Normal)

    # Page indices — mirror the self._tabs registry order built in _initUI.
    # Retained as aliases for the lyrics mixin and existing tests.
    _PAGE_LYRICS = 0
    _PAGE_CREDITS = 1
    _PAGE_ABOUT = 2

    def __init__(self, controller, track=None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self._current_pixmap: QPixmap | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._art_transition_anim: QPropertyAnimation | None = None

        self._sync_dialog = None  # LyricSyncDialog, while a manual-sync session is open

        self._is_synced = False
        self._show_all_lyrics = False  # True while browsing all lines (not following)
        self._lyrics_lines: list[tuple[int, str]] = []
        self._active_idx = -1
        self._last_position_ms = -1

        # Load saved offset from config (stored as tenths of a second, int)
        self._offset_tenths = app_config.get_lyrics_sync_offset()
        self._sync_offset_ms = self._offset_tenths * 100

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

        # Auto-cycle mode state — rotates the right-hand tab stack on a timer.
        self._auto_cycle = False
        self._auto_cycle_timer = QTimer(self)
        self._auto_cycle_timer.setInterval(_AUTO_CYCLE_DWELL_MS)
        self._auto_cycle_timer.timeout.connect(self._advance_auto_cycle)

        # Art slideshow state
        self._art_images: list[tuple[QPixmap, bool, str | None]] = []
        self._art_has_front: bool = False
        self._art_slide_idx: int = 0
        self._art_slide_timer = QTimer(self)
        self._art_slide_timer.setInterval(_COVER_DWELL_MS)
        self._art_slide_timer.timeout.connect(self._advance_art_slide)

        # Background art-cache warming (avoids blocking the UI thread on a
        # cold cache / audio-file decode - see _load_art_from_track)
        self._art_worker: ArtCacheWorker | None = None
        self._art_generation = 0

        self._initUI()
        self._setup_cinema_shortcut()
        self._setup_auto_cycle_shortcut()

        try:
            self.controller.mediaplayer.position_changed.connect(self._on_position_changed)
            self.controller.mediaplayer.position_changed.connect(self._progress.set_position)
        except (AttributeError, RuntimeError) as exc:
            logger.warning(f"NowPlayingView: could not connect position_changed: {exc}")
        try:
            self.controller.mediaplayer.duration_changed.connect(self._progress.set_duration)
        except (AttributeError, RuntimeError) as exc:
            logger.warning(f"NowPlayingView: could not connect duration_changed: {exc}")

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
        """Hide/show player dock, navigation dock, and menu bar; the song
        progress strip shows only while cinema mode is on."""
        self._cinema_mode = not self._cinema_mode
        self._art_column.set_progress_visible(self._cinema_mode)
        try:
            main_win = self.window()
            mb = getattr(main_win, "menuBar", lambda: None)()
            if self._cinema_mode:
                if mb:
                    mb.setVisible(False)
                # Remember whether the queue was actually open so exiting
                # cinema mode doesn't force it open regardless of prior state.
                queue_dock = getattr(main_win, "queue_dock", None)
                self._pre_cinema_queue_visible = queue_dock.isVisible() if queue_dock else False
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
                    main_win.set_queue_visible(getattr(self, "_pre_cinema_queue_visible", False))
        except RuntimeError as exc:
            logger.warning(f"toggle_cinema_mode: {exc}")

    # ── auto-cycle mode ──────────────────────────────────────────────────

    @property
    def auto_cycle(self) -> bool:
        return self._auto_cycle

    def _setup_auto_cycle_shortcut(self):
        """Register Ctrl+Shift++ to toggle auto-cycling of the tab stack."""
        self._auto_cycle_shortcut = QShortcut(QKeySequence("Ctrl+Shift++"), self)
        self._auto_cycle_shortcut.setContext(Qt.ApplicationShortcut)
        self._auto_cycle_shortcut.activated.connect(self.toggle_auto_cycle)

    def toggle_auto_cycle(self):
        """Start/stop rotating the right-hand tab stack on a timer.

        Turning it off leaves the current tab in place — it only stops the
        rotation, it doesn't snap back.
        """
        self._auto_cycle = not self._auto_cycle
        if self._auto_cycle:
            self._auto_cycle_timer.start()
        else:
            self._auto_cycle_timer.stop()
        logger.info(f"NowPlayingView auto-cycle: {'on' if self._auto_cycle else 'off'}")

    def _advance_auto_cycle(self):
        """Switch to the next enabled tab, wrapping past the last one.

        No-op when fewer than two tabs are enabled (e.g. only CREDITS on an
        instrumental track), so the view doesn't thrash a single pane.
        """
        count = len(self._tabs)
        start = self._stack.currentIndex()
        for step in range(1, count + 1):
            nxt = (start + step) % count
            if nxt == start:
                break
            if self._tabs[nxt].button.isEnabled():
                self._switch_tab(nxt)
                return

    # ── build UI ──────────────────────────────────────────────────────────

    @staticmethod
    def _apply_text_shadow(widget, blur=14, y_offset=2, alpha=215):
        """Dark drop shadow so title/artist/album text stays legible when the
        blurred backdrop art itself contains text (e.g. busy cover art)."""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, y_offset)
        effect.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(effect)

    def _initUI(self):
        self.setMinimumSize(760, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setProperty("bgTransparent", True)

        self._backdrop = _BlurredBackdrop(self)
        self._backdrop.lower()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT — album art, slide dots, progress ───────────────────────
        self._art_card = _ArtCard(backdrop=self._backdrop, shadow_pad=_ART_SHADOW_PAD)
        self._slide_dots = _SlideDots()
        self._progress = _ProgressStrip()
        self._art_column = _ArtColumn(self._art_card, self._slide_dots, self._progress)
        # Side margins are at least the shadow pad so the shadow isn't clipped.
        self._art_column.setContentsMargins(32, 36, _ART_SHADOW_PAD, 28)
        self._art_column.setMinimumWidth(260)
        # Song progress shows only in cinema mode (see toggle_cinema_mode).
        self._art_column.set_progress_visible(False)
        root.addWidget(self._art_column, 42)

        # ── RIGHT — metadata + content ───────────────────────────────────
        right_widget = QWidget()
        right_widget.setProperty("bgTransparent", True)
        right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 36, 32, 24)
        right_layout.setSpacing(4)

        # Title — word-wraps up to three lines like a normal label; only a
        # title that would need a fourth line falls back to the panning
        # marquee used by the artist line below.
        self._title_lbl = _AdaptiveTitle("No Track Playing", self._TITLE_FONT, "rgba(230,235,255,0.94)")
        self._apply_text_shadow(self._title_lbl, blur=16, y_offset=2, alpha=225)
        right_layout.addWidget(self._title_lbl)

        # Artist — scrolling marquee so long names pan rather than truncate
        self._artist_marquee = MarqueeLabel("—", self._ARTIST_FONT, "rgba(180,190,240,0.70)")
        # Match the title/album labels' natural line height instead of a
        # hardcoded value, so vertical spacing between the three lines is even.
        self._artist_marquee.setFixedHeight(QFontMetrics(self._ARTIST_FONT).height())
        self._apply_text_shadow(self._artist_marquee, blur=12, y_offset=1, alpha=200)
        right_layout.addWidget(self._artist_marquee)

        # Album — one line: "Album · Subtitle · Year" (see _album_line).
        self._album_lbl = QLabel("—")
        self._album_lbl.setFont(self._ALBUM_FONT)
        self._album_lbl.setProperty("npRole", "album")
        self._album_lbl.setWordWrap(True)
        self._apply_text_shadow(self._album_lbl, blur=10, y_offset=1, alpha=190)
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
        right_layout.addSpacing(16)

        # ── Tab bar (text tabs, accent underline on the active one) ──────
        # Tab buttons are inserted (before this stretch) once the pages and the
        # self._tabs registry are built — see "Tab registry" below.
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.setSpacing(22)
        tab_bar.addStretch()
        right_layout.addLayout(tab_bar)
        right_layout.addSpacing(12)

        # ── Stacked pages ─────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setProperty("bgTransparent", True)
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Page 0: LYRICS — lyric column, countdown, lyric toolbar
        lyrics_page = QWidget()
        lyrics_page.setProperty("bgTransparent", True)
        lp = QVBoxLayout(lyrics_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)

        self._lyric_column = _LyricColumn()
        self._lyric_column.follow_changed.connect(self._on_follow_changed)
        # Same dark halo the title/artist/album labels get — keeps lyrics
        # readable when the backdrop art itself carries text.
        self._apply_text_shadow(self._lyric_column, blur=16, y_offset=2, alpha=200)
        lp.addWidget(self._lyric_column, stretch=1)

        # Countdown label (shown while the next line is ≥5 s away)
        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setAlignment(Qt.AlignCenter)
        self._countdown_lbl.setProperty("npRole", "countdown")
        self._countdown_lbl.setFixedHeight(28)
        self._countdown_lbl.setVisible(False)
        lp.addWidget(self._countdown_lbl)

        lp.addWidget(self._build_lyrics_toolbar())

        # Page 1: CREDITS
        self._credits_panel = _CreditsPanel()

        # Page 2: ABOUT
        self._about_panel = _AboutPanel()

        # ── Tab registry ──────────────────────────────────────────────────
        # List order IS the stack/page index and mirrors the _PAGE_* consts.
        # _switch_tab / updateUI / clearUI iterate self._tabs; the _tab_* and
        # _PAGE_* aliases exist only for the lyrics mixin and existing tests.
        self._tabs: list[_TabSpec] = [
            _TabSpec("lyrics", "LYRICS", lyrics_page),
            _TabSpec("credits", "CREDITS", self._credits_panel, on_show=lambda t: self._credits_panel.load_credits(t), on_hide=self._credits_panel.stop),
            _TabSpec("about", "ABOUT", self._about_panel, on_show=lambda t: self._about_panel.load_about(t)),
        ]

        for idx, spec in enumerate(self._tabs):
            btn = QPushButton(spec.label)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("npTab", True)
            self._set_active(btn, idx == 0)
            btn.clicked.connect(lambda _checked=False, p=idx: self._switch_tab(p))
            spec.button = btn
            tab_bar.insertWidget(idx, btn)
            self._stack.addWidget(spec.widget)

        # Back-compat aliases (lyrics mixin + tests reference these directly).
        self._tab_lyrics = self._tabs[self._PAGE_LYRICS].button
        self._tab_credits = self._tabs[self._PAGE_CREDITS].button
        self._tab_about = self._tabs[self._PAGE_ABOUT].button

        right_layout.addWidget(self._stack, stretch=1)

        root.addWidget(right_widget, 58)

    def _build_lyrics_toolbar(self) -> QWidget:
        """Quiet row under the lyrics: follow toggle, sync-offset stepper,
        manual sync. It lives on the LYRICS page, so it only shows there."""
        self._lyrics_toolbar = QWidget()
        self._lyrics_toolbar.setProperty("bgTransparent", True)
        self._lyrics_toolbar.setVisible(False)
        tb = QHBoxLayout(self._lyrics_toolbar)
        tb.setContentsMargins(0, 8, 0, 0)
        tb.setSpacing(6)

        self._toggle_mode_btn = self._make_toggle("≡  ALL LINES", "Scroll through all lines. Click again to follow the song.", self._on_toggle_lyrics_mode)
        tb.addWidget(self._toggle_mode_btn)
        tb.addStretch(1)

        # Offset stepper: [minus] [⏱ +0.3s] [plus]. The value button resets to 0.
        self._offset_group = QWidget()
        self._offset_group.setProperty("bgTransparent", True)
        og = QHBoxLayout(self._offset_group)
        og.setContentsMargins(0, 0, 0, 0)
        og.setSpacing(2)
        self._offset_minus_btn = self._make_toggle(
            "−",  # noqa: RUF001 (U+2212 minus glyph)
            "Show lyrics 0.1 s later",
            lambda: self._nudge_offset(-1),
        )
        self._offset_value_btn = self._make_toggle("", "Lyric timing offset. Click to reset to 0.", self._reset_offset)
        self._offset_plus_btn = self._make_toggle("+", "Show lyrics 0.1 s earlier", lambda: self._nudge_offset(1))
        for btn in (self._offset_minus_btn, self._offset_plus_btn):
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(400)
            btn.setAutoRepeatInterval(90)
        og.addWidget(self._offset_minus_btn)
        og.addWidget(self._offset_value_btn)
        og.addWidget(self._offset_plus_btn)
        tb.addWidget(self._offset_group)
        tb.addSpacing(8)

        # Opens the manual tap-to-sync dialog (disabled until lyrics load).
        self._manual_sync_btn = self._make_toggle("SYNC…", "Manually sync lyrics line by line", self._on_open_sync_dialog)
        self._manual_sync_btn.setEnabled(False)
        tb.addWidget(self._manual_sync_btn)

        self._refresh_offset_btn()
        return self._lyrics_toolbar

    def _make_toggle(self, text: str, tooltip: str, slot) -> QPushButton:
        """Small [npToggle] pill button. Height is pinned so the toolbar row
        is stable; width follows the size hint (glyph + QSS padding)."""
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setProperty("npToggle", True)
        self._set_active(btn, False)
        btn.clicked.connect(lambda _checked=False: slot())
        return btn

    # ── tab switching ──────────────────────────────────────────────────────

    @staticmethod
    def _set_active(button: QPushButton, active: bool) -> None:
        """Flip a [npTab]/[npToggle] button between its QSS active/inactive states."""
        set_style_property(button, "active", active)

    def _switch_tab(self, page: int):
        spec = self._tabs[page]
        # A disabled tab button (e.g. LYRICS for instrumental tracks) can't be
        # shown, even by an internal or late request.
        if not spec.button.isEnabled():
            return
        self._stack.setCurrentIndex(page)
        for i, s in enumerate(self._tabs):
            self._set_active(s.button, i == page)
            if i != page and s.on_hide:
                s.on_hide()
        if spec.on_show:
            spec.on_show(self.track)

    # ── resize ────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._backdrop.setGeometry(0, 0, self.width(), self.height())

    # ── public API ────────────────────────────────────────────────────────

    def updateUI(self, track):
        try:
            self._close_sync_dialog()

            if not track:
                self.clearUI()
                return

            t0 = time.time()
            logger.info(f"NowPlayingView.updateUI: {getattr(track, 'track_name', '?')}")

            self.track = track

            self._title_lbl.set_text(censor_text(getattr(track, "track_name", None) or "Unknown Title"))

            # Use primary_artist_names property (Oxford-comma formatted)
            artist_str = getattr(track, "primary_artist_names", None)
            if not artist_str:
                # Fallback: first artist in artists proxy
                artists = getattr(track, "artists", None) or []
                artist_str = getattr(artists[0], "artist_name", "") if artists else ""
            self._artist_marquee.set_text(artist_str or "—")

            self._album_lbl.setText(self._album_line(getattr(track, "album", None)))

            self._update_chips(track)
            self._update_lyrics(track)

            # Refresh whichever tab is currently visible (lyrics refreshes via
            # _update_lyrics above and has no on_show).
            visible = self._tabs[self._stack.currentIndex()]
            if visible.on_show:
                visible.on_show(track)

            self._load_art_from_track(track)

            self._progress.reset()
            self._progress.set_duration(self._player_duration())

            logger.debug(f"updateUI TOTAL: {time.time() - t0:.3f}s")

        except Exception as exc:
            # Intentional broad boundary catch: dispatches to several
            # heterogeneous sub-updates (widget text, artist/album ORM
            # lookups, lyrics parsing, credits panel, art loading) on every
            # track change -- this is the single entry point for those and
            # must never leave the UI half-updated, so it falls back to
            # clearUI() instead of propagating.
            logger.error(f"NowPlayingView.updateUI failed: {exc}\n{traceback.format_exc()}")
            self.clearUI()

    def clearUI(self):
        self._close_sync_dialog()
        self._cancel_art_worker()
        self.track = None
        self._title_lbl.set_text("No Track Playing")
        self._artist_marquee.set_text("—")
        self._album_lbl.setText("—")
        self._progress.reset()
        self._tab_lyrics.setEnabled(True)
        self._set_lyrics_mode_none()
        for spec in self._tabs:
            if spec.on_show:
                spec.on_show(None)
        self._chip_row.set_chips([])
        if self.default_art_path and Path(self.default_art_path).exists():
            self._load_art(QPixmap(self.default_art_path))
        else:
            self._load_art(None)

    @staticmethod
    def _album_line(album) -> str:
        """``"Album · Subtitle · Year"``, skipping the parts the album lacks;
        ``"—"`` with no album."""
        if album is None:
            return "—"
        parts = [censor_text(getattr(album, "album_name", "") or "—")]
        subtitle = getattr(album, "album_subtitle", None)
        if subtitle and str(subtitle).strip():
            parts.append(censor_text(str(subtitle).strip()))
        year = getattr(album, "release_year", None)
        if year:
            parts.append(str(year))
        return "  ·  ".join(parts)

    def _player_duration(self) -> int:
        """Current track length from the player, or 0 when it has none yet."""
        duration = getattr(self.controller.mediaplayer, "duration", 0)
        return duration if isinstance(duration, int) else 0

    # ── chips ─────────────────────────────────────────────────────────────

    def _update_chips(self, track):
        visible: list[_Chip] = []

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
                logger.debug(f"_update_chips: skipping chip due to error: {exc}", exc_info=True)

        # ── Basic metadata ─────────────────────────────────────────────────
        _safe(self._chip_bpm, lambda: f"{float(track.bpm):.0f} BPM" if getattr(track, "bpm", None) is not None else None)
        _safe(self._chip_key, lambda: f"{track.key} {(getattr(track, 'mode', '') or '')}".strip() if getattr(track, "key", None) else None)
        _safe(self._chip_timesig, lambda: str(track.primary_time_signature) if getattr(track, "primary_time_signature", None) is not None else None)

        # ── User & library data ────────────────────────────────────────────
        _safe(self._chip_plays, lambda: f"{int(track.play_count)} plays" if getattr(track, "play_count", None) is not None else None)
        _safe(self._chip_genres, lambda: ", ".join(n for n in [getattr(g, "genre_name", "") for g in (getattr(track, "genres", None) or [])[:3]] if n) or None)

        self._chip_row.set_chips(visible)
