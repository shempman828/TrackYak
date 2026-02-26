from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from asset_paths import icon
from config_setup import app_config
from logger_config import logger
from rating_widget import RatingStarsWidget
from track_edit import TrackEditDialog


class PlayerUI(QWidget):
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

        # Track info label - initially empty
        self.track_info_label = QLabel("")
        self.track_info_label.setAlignment(Qt.AlignCenter)
        self.track_info_label.setMinimumWidth(200)
        self.track_info_label.setStyleSheet(
            "QLabel {font-style: italic; font-size: 1.4em; }"
        )

        # Playback buttons
        self.previous_button = self._create_button(
            "previous_button.svg", "Previous Track"
        )
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
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(app_config.get_volume())
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
        self.position_label.setStyleSheet("QLabel { font-size: 1.4em; }")

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
        info_row.addWidget(self.track_info_label)
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
        btn.setStyleSheet("QPushButton { border: none; background: transparent; }")
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

        # ── System media keys (application-wide) ─────────────────────────────
        self.media_play_shortcut = _shortcut(
            Qt.Key_MediaPlay, player.toggle_play_pause, app_wide=True
        )
        self.media_stop_shortcut = _shortcut(
            Qt.Key_MediaStop, player.stop, app_wide=True
        )
        self.media_next_shortcut = _shortcut(
            Qt.Key_MediaNext, player.play_next, app_wide=True
        )
        self.media_prev_shortcut = _shortcut(
            Qt.Key_MediaPrevious, player.play_previous, app_wide=True
        )
        self.media_vol_up_shortcut = _shortcut(
            Qt.Key_VolumeUp, player.increase_volume, app_wide=True
        )
        self.media_vol_down_shortcut = _shortcut(
            Qt.Key_VolumeDown, player.decrease_volume, app_wide=True
        )
        self.media_mute_shortcut = _shortcut(
            Qt.Key_VolumeMute, self._toggle_mute, app_wide=True
        )

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

    def create_dock_widget(self, parent_window=None):
        """Create and configure the dock widget for this player with mini-player support."""
        # Use provided parent_window or fall back to self.parent_window
        dock_parent = parent_window if parent_window else self.parent_window

        dock = QDockWidget("Player", dock_parent)
        dock.setWidget(self)

        # Enable ALL features for maximum flexibility
        dock.setFeatures(QDockWidget.DockWidgetMovable)

        # Connect track change signal
        self.player.track_changed.connect(self._update_track_display)

        return dock

    def _update_track_display(self, file_path: Path):
        """Update UI elements based on track state with robust formatting for classical/non-classical tracks."""
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
                    self.rating_stars.set_rating(
                        getattr(track, "user_rating", 0.0) or 0.0
                    )

                    # Format track info based on classical flag
                    display_text = self._format_track_display(track)
                    self.track_info_label.setText(display_text)
                    self.current_track_info = display_text
                else:
                    self._clear_track_display()
            else:
                self._clear_track_display()
        except Exception as e:
            logger.error(f"Error updating track display: {e}")
            self._clear_track_display()

    def _format_track_display(self, track) -> str:
        """Format track display text based on classical/non-classical classification."""
        try:
            is_classical = getattr(track, "is_classical", False)

            if is_classical:
                return self._format_classical_track(track)
            else:
                return self._format_standard_track(track)
        except Exception as e:
            logger.error(f"Error formatting track display: {e}")
            return "Unknown Track"

    def _format_standard_track(self, track) -> str:
        """Format standard (non-classical) track display."""
        parts = []

        # Track name
        track_name = getattr(track, "track_name", "Unknown Title")
        parts.append(track_name)

        # Artist name
        artist_name = self._get_primary_artist_name(track)
        if artist_name:
            parts.append(f"by {artist_name}")

        # Album and release year
        album_name = getattr(track, "album_name", None)
        release_year = getattr(track, "release_year", None)

        if album_name and release_year:
            parts.append(f"from {album_name} ({release_year})")
        elif album_name:
            parts.append(f"from {album_name}")
        elif release_year:
            parts.append(f"({release_year})")

        return " ".join(parts)

    def _format_classical_track(self, track) -> str:
        """Format classical track display with structured information."""
        lines = []

        # Composer line
        composer_names = self._get_composer_names(track)
        if composer_names:
            lines.append(f"{composer_names}:")

        # Work information line
        work_parts = []

        work_type = getattr(track, "work_type", None)
        if work_type:
            work_parts.append(work_type)

        work_name = getattr(track, "work_name", None)
        if work_name:
            work_parts.append(work_name)

        # Classical catalog information
        catalog_prefix = getattr(track, "classical_catalog_prefix", None)
        catalog_number = getattr(track, "classical_catalog_number", None)
        if catalog_prefix and catalog_number:
            work_parts.append(f"{catalog_prefix} {catalog_number}")
        elif catalog_number:
            work_parts.append(catalog_number)

        # Composition date
        comp_year = getattr(track, "composed_year", None)
        comp_month = getattr(track, "composed_month", None)
        comp_day = getattr(track, "composed_day", None)
        comp_date = self._format_date(comp_year, comp_month, comp_day, "composed")
        if comp_date:
            work_parts.append(comp_date)

        if work_parts:
            lines.append(" ".join(work_parts))

        # Movement line
        movement_parts = []

        movement_number_roman = getattr(track, "movement_number_roman", None)
        if movement_number_roman:
            movement_parts.append(f"{movement_number_roman}.")

        movement_name = getattr(track, "movement_name", None)
        if movement_name:
            movement_parts.append(movement_name)

        if movement_parts:
            lines.append(" ".join(movement_parts))

        # Performance information line
        perf_parts = []

        # Recording date
        rec_year = getattr(track, "recorded_year", None)
        rec_month = getattr(track, "recorded_month", None)
        rec_day = getattr(track, "recorded_day", None)
        rec_date = self._format_date(rec_year, rec_month, rec_day, "recorded")
        if rec_date:
            perf_parts.append(rec_date)

        # First performance date
        first_year = getattr(track, "first_performed_year", None)
        first_month = getattr(track, "first_performed_month", None)
        first_day = getattr(track, "first_performed_day", None)
        first_date = self._format_date(
            first_year, first_month, first_day, "first performed"
        )
        if first_date:
            if perf_parts:
                perf_parts.append(f", {first_date}")
            else:
                perf_parts.append(first_date)

        if perf_parts:
            lines.append(f"({''.join(perf_parts)})")

        # Artist credit (performers)
        performer_name = self._get_primary_artist_name(track)
        if performer_name:
            lines.append(f"Performed by {performer_name}")

        return "\n".join(lines)

    def _get_primary_artist_name(self, track) -> str:
        """Extract primary artist name from track."""
        try:
            # Try primary_artist first
            primary_artist = getattr(track, "primary_artist", None)
            if primary_artist:
                return getattr(primary_artist, "artist_name", "Unknown Artist")

            # Fall back to artists list
            artists = getattr(track, "artists", [])
            if artists:
                first_artist = artists[0]
                return getattr(first_artist, "artist_name", "Unknown Artist")

            return "Unknown Artist"
        except Exception as e:
            logger.error(f"Error getting artist name: {e}")
            return "Unknown Artist"

    def _get_composer_names(self, track) -> str:
        """Extract and format composer names for classical tracks."""
        try:
            composers = []

            # Try composers relationship
            track_composers = getattr(track, "composers", [])
            for composer in track_composers:
                if hasattr(composer, "artist_name"):
                    composers.append(composer.artist_name)

            # Fall back to composer names as string attributes
            if not composers:
                composer_name = getattr(track, "composer_name", None)
                if composer_name:
                    composers.extend(
                        [name.strip() for name in composer_name.split(",")]
                    )

            if composers:
                return ", ".join(composers)
            else:
                return "Unknown Composer"
        except Exception as e:
            logger.error(f"Error getting composer names: {e}")
            return "Unknown Composer"

    def _format_date(self, year, month, day, prefix: str) -> str:
        """Format a date with year, month, day components."""
        if not year:
            return ""

        date_parts = [prefix, year]

        if month:
            month_name = self._get_month_name(month)
            date_parts.append(month_name)

        if day:
            date_parts.append(str(day))

        return ": " + " ".join(date_parts)

    def _get_month_name(self, month) -> str:
        """Convert month number to month name."""
        month_names = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        try:
            month_int = int(month)
            if 1 <= month_int <= 12:
                return month_names[month_int]
        except (ValueError, TypeError):
            pass
        return str(month)

    def _clear_track_display(self):
        """Hide rating and clear track info when no track is playing."""
        self.current_track = None
        self.rating_container.hide()
        self.rating_stars.set_current_file(None)
        self.rating_stars.set_rating(0.0)
        self.track_info_label.setText("")  # Clear the label
        self.track_info_label.setStyleSheet(
            "QLabel { color: #666; font-style: italic; }"
        )
        self.current_track_info = ""

    def adjust_dock_size(self):
        """Adjust dock size to fit content."""
        try:
            ideal_size = self.sizeHint()
            if ideal_size.isValid():
                height = ideal_size.height() + 40
                width = ideal_size.width()
                dock = self.parent().parent() if self.parent() else None
                if isinstance(dock, QDockWidget):
                    dock.setMinimumHeight(height)
                    dock.setMinimumWidth(width)
                    self.setMinimumHeight(ideal_size.height())
                dock.setStyleSheet(
                    "QDockWidget::title { height: 0px; padding: 0px; border: none; }"
                )
        except Exception as e:
            logger.error(f"Error adjusting player dock size: {e}")

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

    def toggle_auto_hide(self, enabled: bool):
        """Enable or disable auto-hide behavior."""
        self.auto_hide_enabled = enabled
        if enabled and not self.is_hovered:
            self.hide_timer.start(self.hide_delay)
        else:
            self.hide_timer.stop()
            self.show_player()

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
            try:
                self.rating_debounce_timer.timeout.disconnect()
            except:  # noqa: E722
                pass  # Was not connected

            self.rating_debounce_timer.timeout.connect(self._commit_rating_to_db)
            self.rating_debounce_timer.start(500)  # 500ms debounce
        else:
            logger.warning("No current track to rate")

    def _commit_rating_to_db(self):
        """Commit rating after debounce."""
        if not (
            hasattr(self, "pending_rating_update")
            and hasattr(self, "pending_track_file")
        ):
            logger.warning("No pending rating to commit")
            return

        if not (self.pending_rating_update and self.pending_track_file):
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
                logger.info(
                    f"Updated rating for '{getattr(track, 'track_name', 'Unknown')}' to {self.pending_rating_update}"
                )
            else:
                logger.warning("Track not found or has no track_id")
        except Exception as e:
            logger.error(f"Error updating track rating: {e}")
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
            self.previous_button.clicked.connect(
                self.controller.mediaplayer.play_previous
            )
            self.next_button.clicked.connect(self.controller.mediaplayer.play_next)

            # Connect volume and seek
            self.volume_slider.valueChanged.connect(
                self.controller.mediaplayer.set_volume
            )
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

        except Exception as e:
            logger.error(f"Error initializing PlayerUI connections: {e}")

    def _on_seek_released(self):
        """Handle seek slider release."""
        if self.player.duration > 0:
            self.seek_requested.emit(self.position_slider.value())

    def _on_repeat_clicked(self):
        """Handle repeat button click."""
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self._update_repeat_button()
        self.repeat_mode_change_requested.emit(self.repeat_mode)

    def on_volume_changed(self, value: int):
        """Adjust player volume and save to config."""
        try:
            self.player.set_volume(value)
            app_config.set_volume(value)
            app_config.save()
        except Exception as e:
            logger.error(f"Error saving volume to config: {e}")

    def update_volume_slider(self, value: int):
        """Sync volume slider without triggering signals."""
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)

    def update_position(self, position: int = None):
        """Update position slider and label."""
        if position is None:
            position = self.controller.mediaplayer.position

        if (
            self.controller.mediaplayer.duration > 0
            and not self.position_slider.isSliderDown()
        ):
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)
        self.position_label.setText(
            f"{self.format_time(position)} / {self.format_time(self.controller.mediaplayer.duration)}"
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

    def handle_seek_move(self, position: int):
        """Preview position during slider drag."""
        if self.player.duration > 0:
            self.position_label.setText(
                f"{self.format_time(position)} / {self.format_time(self.player.duration)}"
            )

    def handle_seek_release(self):
        """Seek player to slider value."""
        if self.player.duration > 0:
            self.player.seek(self.position_slider.value())

    @staticmethod
    def format_time(ms: int) -> str:
        """Convert milliseconds to MM:SS format."""
        minutes, seconds = divmod(ms // 1000, 60)
        return f"{minutes:02}:{seconds:02}"

    def cycle_repeat_mode(self):
        """Cycle through repeat modes."""
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self.repeat_button.setIcon(QIcon(icon(self.repeat_icons[self.repeat_mode])))
        self.repeat_button.setToolTip(self.repeat_labels[self.repeat_mode])
        self.player.set_repeat_mode(self.repeat_mode)

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

    # =========================================================================
    #  Right-click context menu
    # =========================================================================

    def contextMenuEvent(self, event):
        """
        Show a context menu when the user right-clicks anywhere on the player dock.
        Only appears when a track is loaded (self.current_track is not None).
        """
        if not self.current_track:
            return  # Nothing playing — skip the menu

        menu = QMenu(self)

        # Edit Track
        edit_action = QAction("✏️  Edit Track", self)
        edit_action.triggered.connect(self._context_edit_track)
        menu.addAction(edit_action)

        menu.addSeparator()

        # Add to Playlist (submenu)
        playlist_menu = QMenu("➕  Add to Playlist", self)
        self._populate_playlist_submenu(playlist_menu)
        menu.addMenu(playlist_menu)

        # Add to Mood (submenu)
        mood_menu = QMenu("🎭  Add to Mood", self)
        self._populate_mood_submenu(mood_menu)
        menu.addMenu(mood_menu)

        menu.exec_(event.globalPos())

    def _populate_playlist_submenu(self, submenu: QMenu):
        """Fill the Add to Playlist submenu with every playlist in the database."""
        try:
            playlists = self.controller.get.get_all_entities("Playlist")
            if not playlists:
                submenu.addAction("No playlists available").setEnabled(False)
                return
            for playlist in playlists:
                action = QAction(playlist.playlist_name, self)
                action.setData(playlist.playlist_id)
                action.triggered.connect(self._context_add_to_playlist)
                submenu.addAction(action)
        except Exception as e:
            logger.error(f"Error populating playlist submenu: {e}")
            submenu.addAction("Error loading playlists").setEnabled(False)

    def _populate_mood_submenu(self, submenu: QMenu):
        """Fill the Add to Mood submenu with every mood in the database."""
        try:
            moods = self.controller.get.get_all_entities("Mood")
            if not moods:
                submenu.addAction("No moods available").setEnabled(False)
                return
            for mood in moods:
                action = QAction(mood.mood_name, self)
                action.setData(mood.mood_id)
                action.triggered.connect(self._context_add_to_mood)
                submenu.addAction(action)
        except Exception as e:
            logger.error(f"Error populating mood submenu: {e}")
            submenu.addAction("Error loading moods").setEnabled(False)

    def _context_edit_track(self):
        """Open TrackEditDialog for the currently playing track."""
        if not self.current_track:
            return
        try:
            dialog = TrackEditDialog(self.current_track, self.controller, self)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Error opening track editor from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Could not open track editor:\\n{e}")

    def _context_add_to_playlist(self):
        """Add the currently playing track to the playlist chosen in the menu."""
        action = self.sender()
        if not action or not self.current_track:
            return

        playlist_id = action.data()
        track_id = self.current_track.track_id

        try:
            # Check if the track is already in the playlist
            already_in = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id, track_id=track_id
            )
            if already_in:
                logger.warning(
                    "Already Added", "This track is already in that playlist."
                )
                return

            # Find the next available position (append at the end)
            existing = self.controller.get.get_entity_links(
                "PlaylistTracks", playlist_id=playlist_id
            )
            next_position = max((t.position for t in existing), default=0) + 1

            success = self.controller.add.add_entity_link(
                "PlaylistTracks",
                playlist_id=playlist_id,
                track_id=track_id,
                position=next_position,
            )
            if success:
                pass
            else:
                QMessageBox.warning(self, "Failed", "Could not add track to playlist.")

        except Exception as e:
            logger.error(f"Error adding track to playlist from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add to playlist:\\n{e}")

    def _context_add_to_mood(self):
        """Add the currently playing track to the mood chosen in the menu."""
        action = self.sender()
        if not action or not self.current_track:
            return

        mood_id = action.data()
        track_id = self.current_track.track_id

        try:
            # Check if already associated
            existing = self.controller.get.get_entity_links(
                "MoodTrackAssociation", mood_id=mood_id, track_id=track_id
            )
            if existing:
                logger.warning(
                    self, "Already Added", "This track is already in that mood."
                )
                return

            success = self.controller.add.add_entity_link(
                "MoodTrackAssociation",
                mood_id=mood_id,
                track_id=track_id,
            )
            if success:
                pass
            else:
                QMessageBox.warning(self, "Failed", "Could not add track to mood.")

        except Exception as e:
            logger.error(f"Error adding track to mood from player dock: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add to mood:\\n{e}")
