from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.rating_widget import RatingStarsWidget
from src.core.asset_paths import icon
from src.core.config_setup import app_config
from src.core.logger_config import logger
from src.core.status_utility import StatusManager
from src.player.player_context_menu import PlayerContextMenuMixin
from src.player.track_display_formatter import format_track_display
from src.player.track_info_widget import TrackInfoWidget


class PlayerUI(PlayerContextMenuMixin, QWidget):
    """Main player UI widget with playback controls, rating, and volume management."""

    toggle_floating_requested = Signal()
    seek_requested = Signal(int)
    repeat_mode_change_requested = Signal(int)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.player = controller.mediaplayer
        self.parent_window = parent
        self.current_track = None

        # Dragging support for mini-player
        self.drag_enabled = False
        self.drag_position = None
        # Auto-hide settings
        self.auto_hide_enabled = False
        self.is_hovered = False
        self.is_visible = True
        self.hide_delay = 2000  # 2 seconds delay before hiding

        # Create timer but DON'T connect yet
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)

        # Rating stars and other initialization...
        self.rating_stars = RatingStarsWidget()

        from src.lyrics.lyrics_search import LyricSearchThread

        self._lyric_thread = LyricSearchThread(parent=self)
        self._lyric_thread.lyrics_ready.connect(self._on_lyrics_ready)
        self._lyric_thread.lyrics_not_found.connect(self._on_lyrics_not_found)
        self._lyric_thread.error_occurred.connect(self._on_lyric_error)

        # Initialize UI and connections
        self.init_ui()
        self.init_connections()
        self.setup_timers()
        self.setup_keyboard_shortcuts()
        self.setMouseTracking(True)

        # NOW connect the timer after everything is initialized
        self.hide_timer.timeout.connect(self.hide_player)

    def init_ui(self) -> None:
        """Set up horizontal playback controls, sliders, volume, repeat, and rating."""
        logger.info("Initializing PlayerUI...")

        # Repeat state
        self.repeat_mode: int = 0
        self.repeat_labels = ["Repeat: None", "Repeat: One", "Repeat: All"]
        self.repeat_icons = ["repeat_none.svg", "repeat_one.svg", "repeat_all.svg"]

        # Track info widget — scrolling title + clickable artist/album
        self.track_info_widget = TrackInfoWidget(self.controller, self)
        self.track_info_widget.setMinimumWidth(200)
        # Keep a .track_info_label alias so any other code that reads
        # .track_info_label.text() still works without breaking.
        self.track_info_label = self.track_info_widget.title_label

        # Playback buttons
        self.previous_button = self._create_button("previous_button.svg", "Previous Track")
        self.play_button = self._create_button("play_button.svg", "Play")
        self.pause_button = self._create_button("pause_button.svg", "Pause")
        self.stop_button = self._create_button("stop_button.svg", "Stop")
        self.next_button = self._create_button("next_button.svg", "Next Track")
        self.pause_button.hide()

        # Repeat button
        self.repeat_button = QPushButton()
        self.repeat_button.setCheckable(True)
        self._update_repeat_button()
        self.repeat_button.setFixedSize(48, 48)
        self.repeat_button.setIconSize(QSize(32, 32))

        # Volume slider
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(app_config.get_volume())
        self._update_volume_tooltip(self.volume_slider.value())
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setSingleStep(5)

        # Position slider
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 100)
        self.position_slider.setEnabled(False)
        self.position_slider.setMinimumWidth(200)

        # Position label
        self.position_label = QLabel("0:00 / 0:00")
        self.position_label.setAlignment(Qt.AlignCenter)
        self.position_label.setFixedWidth(100)
        self.position_label.setObjectName("PlayerPositionLabel")

        # Main horizontal layout
        layout = QHBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 6, 12, 6)

        # Section 1: Playback controls
        for btn in [
            self.previous_button,
            self.play_button,
            self.pause_button,
            self.stop_button,
            self.next_button,
        ]:
            layout.addWidget(btn)
        layout.addSpacing(8)

        # Section 2: Track info and progress
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setSpacing(4)
        center_layout.setContentsMargins(0, 0, 0, 0)

        # Track info row
        info_row = QHBoxLayout()
        info_row.addStretch()
        info_row.addWidget(self.track_info_widget)
        info_row.addStretch()

        # Progress row
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.position_slider)
        progress_row.addWidget(self.position_label)

        center_layout.addLayout(info_row)
        center_layout.addLayout(progress_row)
        layout.addWidget(center_widget, 1)  # Stretch factor

        # Section 3: Rating and volume
        right_widget = QWidget()
        right_layout = QHBoxLayout(right_widget)
        right_layout.setSpacing(8)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Rating section - initially hidden
        rating_container = QWidget()
        rating_layout = QHBoxLayout(rating_container)
        rating_layout.setSpacing(4)
        rating_layout.setContentsMargins(0, 0, 0, 0)
        rating_layout.addWidget(QLabel("Rating:"))
        rating_layout.addWidget(self.rating_stars)
        rating_container.hide()
        self.rating_container = rating_container

        right_layout.addWidget(rating_container)
        right_layout.addSpacing(16)
        right_layout.addWidget(self.volume_slider)
        right_layout.addWidget(self.repeat_button)
        layout.addWidget(right_widget)

        logger.info("Player dock setup complete.")

    def _create_button(self, icon_file: str, tooltip: str) -> QPushButton:
        """Helper to create a fixed-size button with scaled icon."""
        btn = QPushButton()
        btn.setIcon(QIcon(icon(icon_file)))
        btn.setIconSize(QSize(48, 48))  # <-- scale icon inside button
        btn.setToolTip(tooltip)
        btn.setFixedSize(64, 64)
        btn.setProperty("playerCtrlBtn", True)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _update_repeat_button(self) -> None:
        """Update the repeat button icon and tooltip based on current mode."""
        self.repeat_button.setIcon(QIcon(icon(self.repeat_icons[self.repeat_mode])))
        self.repeat_button.setToolTip(self.repeat_labels[self.repeat_mode])

    def setup_keyboard_shortcuts(self):
        """
        Set up keyboard shortcuts for media control.

        Two layers of shortcuts are registered:
          1. Standard keyboard combos (Ctrl/Shift + arrow keys, Space)
             — work when the app window is focused.
          2. System media keys (⏮ ⏯ ⏭ 🔇 🔊 on dedicated media keyboards
             and all Apple keyboards) — registered with ApplicationShortcut
             so they work even when a different widget inside the app has focus.
        """
        player = self.controller.mediaplayer

        def _shortcut(key, slot, app_wide=False):
            """Helper: create a shortcut, store it, and connect it."""
            sc = QShortcut(QKeySequence(key), self)
            if app_wide:
                sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)
            return sc

        # ── Standard keyboard shortcuts ───────────────────────────────────────
        self.space_shortcut = _shortcut("Space", player.toggle_play_pause)
        self.stop_shortcut = _shortcut("Ctrl+.", player.stop)
        self.next_shortcut = _shortcut("Ctrl+Right", player.play_next)
        self.prev_shortcut = _shortcut("Ctrl+Left", player.play_previous)
        self.vol_up_shortcut = _shortcut("Ctrl+Up", player.increase_volume)
        self.vol_down_shortcut = _shortcut("Ctrl+Down", player.decrease_volume)
        self.seek_forward_shortcut = _shortcut("Shift+Right", player.seek_forward)
        self.seek_backward_shortcut = _shortcut("Shift+Left", player.seek_backward)
        self.rating_up_shortcut = _shortcut("Ctrl+Shift+Up", lambda: self._adjust_rating(0.5))
        self.rating_down_shortcut = _shortcut("Ctrl+Shift+Down", lambda: self._adjust_rating(-0.5))
        self.lyrics_search_shortcut = _shortcut("Ctrl+Shift+L", self._context_search_lyrics)

        # ── System media keys (application-wide) ─────────────────────────────
        self.media_play_shortcut = _shortcut(
            Qt.Key_MediaPlay, player.toggle_play_pause, app_wide=True
        )
        self.media_stop_shortcut = _shortcut(Qt.Key_MediaStop, player.stop, app_wide=True)
        self.media_next_shortcut = _shortcut(Qt.Key_MediaNext, player.play_next, app_wide=True)
        self.media_prev_shortcut = _shortcut(
            Qt.Key_MediaPrevious, player.play_previous, app_wide=True
        )
        self.media_vol_up_shortcut = _shortcut(
            Qt.Key_VolumeUp, player.increase_volume, app_wide=True
        )
        self.media_vol_down_shortcut = _shortcut(
            Qt.Key_VolumeDown, player.decrease_volume, app_wide=True
        )
        self.media_mute_shortcut = _shortcut(Qt.Key_VolumeMute, self._toggle_mute, app_wide=True)

    def _toggle_mute(self):
        """Toggle mute: set volume to 0 or restore previous level."""
        player = self.controller.mediaplayer
        if not hasattr(self, "_pre_mute_volume"):
            self._pre_mute_volume = None

        if player.volume_level > 0:
            # Mute: remember current volume, set to 0
            self._pre_mute_volume = player.volume_level
            player.set_volume(0)
        else:
            # Unmute: restore saved volume (default to 75 if nothing saved)
            restore = self._pre_mute_volume if self._pre_mute_volume else 75
            player.set_volume(restore)
            self._pre_mute_volume = None

    def _update_track_display(self, file_path: Path):
        """Update UI elements based on track state.

        Uses robust formatting for classical and non-classical tracks.
        """
        # Reset position display immediately on every track change
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(0)
        self.position_slider.blockSignals(False)
        self.position_label.setText("0:00 / 0:00")

        try:
            if file_path:
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(file_path)
                )
                if track:
                    # Show rating and update it
                    self.current_track = track
                    self.rating_container.show()
                    self.rating_stars.set_current_file(file_path)
                    self.rating_stars.set_rating(getattr(track, "user_rating", 0.0) or 0.0)

                    # Update the rich track info widget (title + artist + album)
                    self.track_info_widget.update_track(track)
                    # Also keep the plain-text fallback for anything that reads it
                    self.current_track_info = format_track_display(track)
                else:
                    self._clear_track_display()
            else:
                self._clear_track_display()
        except (SQLAlchemyError, RuntimeError, AttributeError) as e:
            logger.error(f"Error updating track display: {e}")
            self._clear_track_display()

    def _clear_track_display(self):
        """Hide rating and clear track info when no track is playing."""
        self.current_track = None
        self.rating_container.hide()
        self.rating_stars.set_current_file(None)
        self.rating_stars.set_rating(0.0)
        self.track_info_widget.clear()
        self.current_track_info = ""

    def setup_timers(self):
        """Set up periodic UI updates and auto-hide timer."""
        # Position update timer
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(500)
        self.update_timer.timeout.connect(self.update_position)
        self.hide_timer.setInterval(self.hide_delay)

        # Rating debounce timer
        self.rating_debounce_timer = QTimer(self)
        self.rating_debounce_timer.setSingleShot(True)

    def show_player(self):
        """Show the player bar."""
        if not self.is_visible:
            self.show()
            self.is_visible = True

    def hide_player(self):
        """Hide the player bar (only if auto-hide enabled and not hovered)."""
        if self.auto_hide_enabled and not self.is_hovered and self.is_visible:
            self.hide()
            self.is_visible = False

    def _adjust_rating(self, delta: float):
        """Increase or decrease the current track's rating by `delta` (0.5 steps)."""
        current_file = getattr(self.player, "current_file", None)
        if not current_file:
            return
        new_rating = max(0.0, min(10.0, self.rating_stars.rating + delta))
        self.rating_stars.set_rating(new_rating)
        self.on_rating_changed(new_rating)

    def on_rating_changed(self, rating: float):
        """Handle user rating with debounce."""
        current_file = getattr(self.player, "current_file", None)
        if current_file:
            logger.debug(f"Rating changed to {rating} for {current_file}")
            self.pending_rating_update = rating
            self.pending_track_file = current_file

            # Stop any existing timer and start fresh
            if self.rating_debounce_timer.isActive():
                self.rating_debounce_timer.stop()

            # Connect the timeout signal if not already connected
            with suppress(RuntimeError):  # Was not connected
                self.rating_debounce_timer.timeout.disconnect()

            self.rating_debounce_timer.timeout.connect(self._commit_rating_to_db)
            self.rating_debounce_timer.start(500)  # 500ms debounce
        else:
            logger.warning("No current track to rate")

    def _commit_rating_to_db(self):
        """Commit rating after debounce."""
        if not (hasattr(self, "pending_rating_update") and hasattr(self, "pending_track_file")):
            logger.warning("No pending rating to commit")
            return

        if self.pending_rating_update is None or self.pending_track_file is None:
            logger.warning("Pending rating or track file is None")
            return

        try:
            logger.debug(
                f"Committing rating {self.pending_rating_update} for {self.pending_track_file}"
            )

            track = self.controller.get.get_entity_object(
                "Track", track_file_path=str(self.pending_track_file)
            )
            if track and getattr(track, "track_id", None):
                self.controller.update.update_entity(
                    "Track", track.track_id, user_rating=self.pending_rating_update
                )
                track_name = getattr(track, "track_name", "Unknown")
                logger.info(f"Updated rating for '{track_name}' to {self.pending_rating_update}")
            else:
                logger.warning("Track not found or has no track_id")
                StatusManager.show_message(
                    "Could not save rating: track not found in library.", 5000
                )
        except SQLAlchemyError as e:
            logger.error(f"Error updating track rating: {e}")
            StatusManager.show_message(f"Could not save rating: {e}", 5000)
        finally:
            self.pending_rating_update = None
            self.pending_track_file = None

    def init_connections(self):
        """Connect all buttons and player signals."""
        try:
            # Connect UI actions to player methods directly
            self.play_button.clicked.connect(self.controller.mediaplayer.play)
            self.pause_button.clicked.connect(self.controller.mediaplayer.pause)
            self.stop_button.clicked.connect(self.controller.mediaplayer.stop)
            self.previous_button.clicked.connect(self.controller.mediaplayer.play_previous)
            self.next_button.clicked.connect(self.controller.mediaplayer.play_next)

            # Connect volume and seek
            self.volume_slider.valueChanged.connect(self.controller.mediaplayer.set_volume)
            self.volume_slider.valueChanged.connect(self._update_volume_tooltip)
            self.position_slider.sliderPressed.connect(self._on_seek_pressed)
            self.position_slider.sliderReleased.connect(self._on_seek_released)
            self.repeat_button.clicked.connect(self._on_repeat_clicked)

            # Connect PLAYER signals to UI updates
            self.player.position_changed.connect(self.update_position)
            self.player.duration_changed.connect(self.update_duration)
            self.player.state_changed.connect(self.handle_state_change)
            self.player.volume_changed.connect(self.update_volume_slider)
            self.player.track_changed.connect(self._update_track_display)

            self.repeat_mode_change_requested.connect(self.player.set_repeat_mode)
            self.seek_requested.connect(self.player.seek)
            self.rating_stars.rating_changed.connect(self.on_rating_changed)

        except (RuntimeError, AttributeError) as e:
            logger.error(f"Error initializing PlayerUI connections: {e}")

    def _on_seek_pressed(self):
        """Remember which track was playing when the drag started."""
        self._seek_track_file = self.player.current_file

    def _on_seek_released(self):
        """Handle seek slider release."""
        # If the track changed (auto-advance/skip) while the slider was
        # held down, the slider's value belongs to the old track — discard it
        # instead of applying a stale position to the new one.
        if self.player.current_file != getattr(self, "_seek_track_file", None):
            return
        if self.player.duration > 0:
            self.seek_requested.emit(self.position_slider.value())

    def _on_repeat_clicked(self):
        """Handle repeat button click."""
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self._update_repeat_button()
        self.repeat_mode_change_requested.emit(self.repeat_mode)

    def update_volume_slider(self, value: int):
        """Sync volume slider without triggering signals."""
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)
        self._update_volume_tooltip(value)

    def _update_volume_tooltip(self, value: int):
        """Show the current volume percentage in the slider's tooltip."""
        self.volume_slider.setToolTip(f"Volume: {value}%")

    def update_position(self, position: int | None = None):
        """Update position slider and label."""
        mediaplayer = self.controller.mediaplayer
        if position is None:
            position = mediaplayer.position

        if mediaplayer.duration > 0 and not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)
        self.position_label.setText(
            f"{self.format_time(position)} / {self.format_time(mediaplayer.duration)}"
        )

    def update_duration(self, duration: int):
        """Update duration label and enable slider."""
        if duration > 0:
            self.position_slider.setEnabled(True)
            self.position_slider.setRange(0, duration)
            self.position_label.setText(f"0:00 / {self.format_time(duration)}")
        else:
            self.position_slider.setEnabled(False)
            self.position_label.setText("0:00 / 0:00")

    @staticmethod
    def format_time(ms: int) -> str:
        """Convert milliseconds to MM:SS format."""
        minutes, seconds = divmod(ms // 1000, 60)
        return f"{minutes:02}:{seconds:02}"

    def handle_state_change(self, state: str):
        """Update UI button states based on player state."""
        is_playing = state == "playing"
        self.play_button.setVisible(not is_playing)
        self.pause_button.setVisible(is_playing)
        self.stop_button.setEnabled(state != "stopped")
        self.previous_button.setEnabled(bool(self.player.queue_manager.queue))
        self.next_button.setEnabled(bool(self.player.queue_manager.queue))

        # Auto-show when playback starts if auto-hide is enabled
        if is_playing and self.auto_hide_enabled and not self.is_visible:
            self.show_player()

    def cleanup(self):
        """Stop any in-flight background work before the app closes."""
        self._lyric_thread.stop()
