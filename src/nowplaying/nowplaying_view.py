"""NowPlayingView module — Cinematic redesign."""

from pathlib import Path
import time
import traceback

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.album.album_art_worker import ArtCacheWorker
from src.common.style_utils import set_style_property
from src.foundation.asset_paths import asset
from src.foundation.censor import censor_text
from src.foundation.config_setup import app_config
from src.foundation.logger_config import logger
from src.nowplaying.nowplaying_art import _ArtCard
from src.nowplaying.nowplaying_art_slideshow import _COVER_DWELL_MS, NowPlayingArtMixin
from src.nowplaying.nowplaying_backdrop import _BlurredBackdrop
from src.nowplaying.nowplaying_chip import _Chip, _ScrollingChipRow
from src.nowplaying.nowplaying_credits import _CreditsPanel
from src.nowplaying.nowplaying_karaoke import _KaraokeLine
from src.nowplaying.nowplaying_lyrics import NowPlayingLyricsMixin
from src.nowplaying.nowplaying_marquee import FadedScrollArea, MarqueeLabel

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

# Debounce delay (ms) before persisting the sync-offset slider to config.
_OFFSET_DEBOUNCE_MS = 600

# Tab and toggle button visuals live in themes/dark_mode.qss under the
# [npTab="true"] / [npToggle="true"] / [active=...] selectors — see _set_active().


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
    _PLAIN_FONT = QFont("Cambria", 12, QFont.Normal)
    _PREVIEW_FONT = QFont("Cambria", 14, QFont.Normal)

    # Upcoming-lyric preview stack: always show at least _PREVIEW_MIN_ROWS, and
    # up to _PREVIEW_MAX_ROWS when the karaoke block is tall enough to fit them
    # (see _recalc_preview_capacity).
    _PREVIEW_MIN_ROWS = 3
    _PREVIEW_MAX_ROWS = 6

    _PAGE_LYRICS = 0
    _PAGE_CREDITS = 1

    def __init__(self, controller, track=None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self._current_pixmap: QPixmap | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._art_transition_anim: QPropertyAnimation | None = None

        self._is_synced = False
        self._show_all_lyrics = False  # Toggle: karaoke vs full plain view
        self._lyrics_lines: list[tuple[int, str]] = []
        self._active_idx = -1
        self._last_position_ms = -1
        self._preview_capacity = self._PREVIEW_MIN_ROWS

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

        try:
            self.controller.mediaplayer.position_changed.connect(self._on_position_changed)
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
        self._apply_text_shadow(self._title_lbl, blur=16, y_offset=2, alpha=225)
        right_layout.addWidget(self._title_lbl)

        # Artist — scrolling marquee so long names pan rather than truncate
        self._artist_marquee = MarqueeLabel("—", self._ARTIST_FONT, "rgba(180,190,240,0.70)")
        # Match the title/album labels' natural line height instead of a
        # hardcoded value, so vertical spacing between the three lines is even.
        self._artist_marquee.setFixedHeight(QFontMetrics(self._ARTIST_FONT).height())
        self._apply_text_shadow(self._artist_marquee, blur=12, y_offset=1, alpha=200)
        right_layout.addWidget(self._artist_marquee)

        # Album
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
        # Pin height only — the shared [npToggle] QSS reserves 8px of horizontal
        # padding each side, so a 24px-wide square clips the glyph. Let the width
        # follow the size hint (glyph + padding + border).
        self._sync_toggle_btn.setFixedHeight(24)
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
        # Same dark halo the title/artist/album labels get — keeps the current
        # karaoke line readable when the backdrop art itself carries text.
        self._apply_text_shadow(self._karaoke_lbl, blur=18, y_offset=2, alpha=230)

        # Upcoming lyric lines — a preview stack shown below the current karaoke
        # line, each row fainter than the one above it (see the npRole="nextLyric"
        # / "nextLyric2" / "nextLyric3" rules in the QSS). The pool is built at
        # the tallest size; how many actually show is fitted to the block height
        # by _recalc_preview_capacity(). Rows past the third reuse the faintest
        # role.
        self._next_lyric_lbls: list[QLabel] = []
        for i in range(self._PREVIEW_MAX_ROWS):
            role = "nextLyric" if i == 0 else "nextLyric2" if i == 1 else "nextLyric3"
            lbl = QLabel("")
            lbl.setFont(self._PREVIEW_FONT)
            lbl.setProperty("npRole", role)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setVisible(False)
            # Lighter halo than the current line — enough to lift the faint
            # preview rows off busy cover art without muddying them.
            self._apply_text_shadow(lbl, blur=12, y_offset=1, alpha=160)
            self._next_lyric_lbls.append(lbl)

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
        for lbl in self._next_lyric_lbls:
            kb.addWidget(lbl)
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

        self._offset_lbl = QLabel("Sync  −0.5s")  # noqa: RUF001 (U+2212 minus glyph)
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
        # LYRICS is disabled for instrumental tracks — ignore any internal or
        # late request to show it while the tab button is disabled.
        if page == self._PAGE_LYRICS and not self._tab_lyrics.isEnabled():
            return
        self._stack.setCurrentIndex(page)
        if page == self._PAGE_LYRICS:
            self._set_active(self._tab_lyrics, True)
            self._set_active(self._tab_credits, False)
            self._credits_panel.stop()
        else:
            self._set_active(self._tab_lyrics, False)
            self._set_active(self._tab_credits, True)
            self._credits_panel.load_credits(self.track)

    # ── resize ────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._backdrop.setGeometry(0, 0, self.width(), self.height())
        if (
            self._recalc_preview_capacity()
            and self._is_synced
            and not self._show_all_lyrics
            and self._active_idx >= 0
        ):
            self._update_next_lyric_lbl(self._active_idx)

    def _recalc_preview_capacity(self) -> bool:
        """Fit the upcoming-lyric preview stack to the karaoke block's height.

        Returns True when the row count changed. Called on resize and before
        every preview refill so a taller panel shows more upcoming lines,
        bounded by _PREVIEW_MIN_ROWS.._PREVIEW_MAX_ROWS.
        """
        block_h = self._karaoke_block.height()
        if block_h <= 0:
            return False
        # kb layout spacing is 6px; reserve up to two wrapped rows for the
        # current line's larger font before dividing the rest into preview rows.
        row_h = QFontMetrics(self._PREVIEW_FONT).lineSpacing() + 6
        current_h = QFontMetrics(self._karaoke_lbl.font()).lineSpacing() * 2
        fits = (block_h - current_h - 6) // row_h
        new_cap = max(self._PREVIEW_MIN_ROWS, min(self._PREVIEW_MAX_ROWS, int(fits)))
        if new_cap == self._preview_capacity:
            return False
        self._preview_capacity = new_cap
        return True

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
            logger.error(f"NowPlayingView.updateUI failed: {exc}\n{traceback.format_exc()}")
            self.clearUI()

    def clearUI(self):
        self._cancel_art_worker()
        self.track = None
        self._title_lbl.setText("No Track Playing")
        self._artist_marquee.set_text("—")
        self._album_lbl.setText("—")
        self._tab_lyrics.setEnabled(True)
        self._set_lyrics_mode_none()
        self._credits_panel.load_credits(None)
        self._chip_row.set_chips([])
        if self.default_art_path and Path(self.default_art_path).exists():
            self._load_art(QPixmap(self.default_art_path))
        else:
            self._load_art(None)

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
        _safe(
            self._chip_bpm,
            lambda: (
                f"{float(track.bpm):.0f} BPM" if getattr(track, "bpm", None) is not None else None
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
