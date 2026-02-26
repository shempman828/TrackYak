# mini_player_window.py

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.asset_paths import icon
from src.logger_config import logger


class MiniPlayerWindow(QWidget):
    """A floating mini-player window that can be used independently of the main UI."""

    def __init__(self, controller, parent=None):
        super().__init__()
        self.controller = controller
        self.player = controller.mediaplayer

        # Window properties - KEY CHANGES
        self.setWindowTitle("Mini Player")
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowType.Window  # Explicit standalone window
        )

        # These attributes are CRITICAL for independence
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setAttribute(Qt.WA_QuitOnClose, False)  # Don't quit app when closed

        self.setMinimumSize(400, 120)

        # Track info
        self.current_track_file = None
        self.track_info = ""

        # Auto-hide options
        self.auto_hide_enabled = False
        self.auto_hide_timer = QTimer(self)
        self.auto_hide_timer.timeout.connect(self.hide_window_edges)

        # Initialize UI
        self.init_ui()
        self.init_connections()

        # Store controller reference but don't make it parent
        self._controller = controller

    def init_ui(self):
        """Set up the mini-player UI layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create a custom title bar for dragging
        self.title_bar = QWidget()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(30)
        self.title_bar.setCursor(Qt.OpenHandCursor)
        self.title_bar.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.title_bar.installEventFilter(self)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)

        # Title label
        title_label = QLabel("TrackYak Mini Player")

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setObjectName("titleCloseButton")
        close_btn.clicked.connect(self.hide)

        title_layout.addWidget(title_label)
        title_layout.addWidget(spacer)
        title_layout.addWidget(close_btn)

        main_layout.addWidget(self.title_bar)

        # Main content container (has rounded corners)
        self.content_widget = QWidget()
        self.content_widget.setObjectName("contentWidget")
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setSpacing(8)
        content_layout.setContentsMargins(12, 12, 12, 12)

        # Top row: Close button and track info
        top_row = QHBoxLayout()

        # Close button
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(24, 24)
        self.close_button.setObjectName("closeButton")
        self.close_button.clicked.connect(self.hide)

        # Track info label (truncated)
        self.track_label = QLabel("No track playing")
        self.track_label.setObjectName("trackLabel")
        self.track_label.setAlignment(Qt.AlignCenter)
        self.track_label.setWordWrap(True)

        # Empty spacer
        top_row.addWidget(self.close_button)
        top_row.addStretch()
        top_row.addWidget(self.track_label, 1)
        top_row.addStretch()
        top_row.addWidget(QLabel())  # Placeholder for symmetry

        # Middle row: Position slider
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setObjectName("positionSlider")
        self.position_slider.setEnabled(False)
        self.position_slider.setRange(0, 100)

        # Position label
        self.position_label = QLabel("0:00 / 0:00")
        self.position_label.setObjectName("positionLabel")
        self.position_label.setAlignment(Qt.AlignCenter)

        # Bottom row: Playback controls
        bottom_row = QHBoxLayout()

        # Previous button
        self.prev_button = self._create_mini_button("previous_button.svg", "Previous")

        # Play/Pause button
        self.play_button = self._create_mini_button("play_button.svg", "Play")
        self.pause_button = self._create_mini_button("pause_button.svg", "Pause")
        self.pause_button.hide()

        # Stop button
        self.stop_button = self._create_mini_button("stop_button.svg", "Stop")

        # Next button
        self.next_button = self._create_mini_button("next_button.svg", "Next")

        # Volume control
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.player.volume)
        self.volume_slider.setToolTip("Volume")

        # Add widgets to bottom row
        bottom_row.addStretch()
        bottom_row.addWidget(self.prev_button)
        bottom_row.addWidget(self.play_button)
        bottom_row.addWidget(self.pause_button)
        bottom_row.addWidget(self.stop_button)
        bottom_row.addWidget(self.next_button)
        bottom_row.addStretch()
        bottom_row.addWidget(self.volume_slider)

        # Assemble content layout
        content_layout.addLayout(top_row)
        content_layout.addWidget(self.position_slider)
        content_layout.addWidget(self.position_label, 0, Qt.AlignCenter)
        content_layout.addLayout(bottom_row)

        # Add content widget to main layout
        main_layout.addWidget(self.content_widget)

    def _create_mini_button(self, icon_file: str, tooltip: str) -> QPushButton:
        """Create a mini button with icon."""
        btn = QPushButton()
        btn.setIcon(QIcon(icon(icon_file)))
        btn.setIconSize(QSize(24, 24))
        btn.setToolTip(tooltip)
        btn.setFixedSize(36, 36)
        btn.setObjectName("miniButton")
        return btn

    def init_connections(self):
        """Connect player signals and UI controls."""
        # Connect UI controls to player
        self.play_button.clicked.connect(self.player.play)
        self.pause_button.clicked.connect(self.player.pause)
        self.stop_button.clicked.connect(self.player.stop)
        self.prev_button.clicked.connect(self.player.play_previous)
        self.next_button.clicked.connect(self.player.play_next)
        self.volume_slider.valueChanged.connect(self.player.set_volume)
        self.position_slider.sliderReleased.connect(self._on_seek_released)

        # Connect player signals to UI updates
        self.player.state_changed.connect(self._on_player_state_changed)
        self.player.position_changed.connect(self.update_position)
        self.player.duration_changed.connect(self.update_duration)
        self.player.volume_changed.connect(self.update_volume_slider)
        self.player.track_changed.connect(self._on_track_changed)

        # Connect close button
        self.close_button.clicked.connect(self.hide)

    def _on_player_state_changed(self, state: str):
        """Update play/pause buttons based on player state."""
        is_playing = state == "playing"
        self.play_button.setVisible(not is_playing)
        self.pause_button.setVisible(is_playing)

        # Update window title with playing state
        if is_playing and self.track_info:
            self.setWindowTitle(f"▶ {self.track_info[:30]}...")
        elif not is_playing and self.track_info:
            self.setWindowTitle(f"⏸ {self.track_info[:30]}...")
        else:
            self.setWindowTitle("Mini Player")

    def _on_track_changed(self, file_path: Path):
        """Update track information when track changes."""
        self.current_track_file = file_path

        if file_path:
            try:
                track = self.controller.get.get_entity_object(
                    "Track", track_file_path=str(file_path)
                )
                if track:
                    # Get comprehensive track info using same logic as dock player
                    display_text = self._format_track_display(track)

                    # For mini player, we might want a shorter version
                    # Let's get the first line or truncate appropriately
                    lines = display_text.split("\n")
                    if lines and lines[0]:
                        short_display = lines[0]
                        # Truncate if too long
                        if len(short_display) > 50:
                            short_display = short_display[:47] + "..."
                    else:
                        short_display = "Unknown Track"

                    self.track_info = short_display
                    self.track_label.setText(short_display)

                    # Update window title with full track name for better context
                    track_name = getattr(track, "track_name", "Unknown Track")
                    if len(track_name) > 30:
                        track_name = track_name[:27] + "..."

                    if self.player.state == "playing":
                        self.setWindowTitle(f"▶ {track_name}")
                    else:
                        self.setWindowTitle(f"⏸ {track_name}")
                else:
                    self._clear_track_info()
            except Exception as e:
                logger.error(f"Error updating mini-player track info: {e}")
                self._clear_track_info()
        else:
            self._clear_track_info()

    def _clear_track_info(self):
        """Clear track information."""
        self.track_info = ""
        self.track_label.setText("No track playing")
        self.setWindowTitle("Mini Player")

    def update_position(self, position: int = None):
        """Update position slider and label."""
        if position is None:
            position = self.player.position

        if self.player.duration > 0 and not self.position_slider.isSliderDown():
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position)
            self.position_slider.blockSignals(False)

        if self.player.duration > 0:
            self.position_label.setText(
                f"{self._format_time(position)} / {self._format_time(self.player.duration)}"
            )
        else:
            self.position_label.setText("0:00 / 0:00")

    def update_duration(self, duration: int):
        """Update duration and enable position slider."""
        if duration > 0:
            self.position_slider.setEnabled(True)
            self.position_slider.setRange(0, duration)
        else:
            self.position_slider.setEnabled(False)
        self.update_position()

    def update_volume_slider(self, value: int):
        """Update volume slider without triggering signals."""
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)

    def _on_seek_released(self):
        """Handle seek slider release."""
        if self.player.duration > 0:
            self.player.seek(self.position_slider.value())

    @staticmethod
    def _format_time(ms: int) -> str:
        """Convert milliseconds to MM:SS format."""
        minutes, seconds = divmod(ms // 1000, 60)
        return f"{minutes:02}:{seconds:02}"

    def enterEvent(self, event):
        """Handle mouse enter for auto-hide."""
        if self.auto_hide_enabled:
            self.auto_hide_timer.stop()
            self.show_full_window()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Handle mouse leave for auto-hide."""
        if self.auto_hide_enabled:
            self.auto_hide_timer.start(1000)  # Hide after 1 second
        super().leaveEvent(event)

    def show_full_window(self):
        """Show the full window."""
        self.showNormal()

    def hide_window_edges(self):
        """Hide window edges (for auto-hide feature)."""
        screen_geometry = self.screen().availableGeometry()

        # Move to edge but keep a small part visible
        if self.pos().y() < screen_geometry.height() // 2:
            # Top edge
            self.move(self.pos().x(), 2 - self.height() + 20)
        else:
            # Bottom edge
            self.move(self.pos().x(), screen_geometry.height() - 20)

    def toggle_auto_hide(self, enabled: bool):
        """Enable or disable auto-hide feature."""
        self.auto_hide_enabled = enabled
        if enabled:
            self.auto_hide_timer.start(2000)  # Start with 2 second delay
        else:
            self.auto_hide_timer.stop()
            self.show_full_window()

    def show(self):
        """Show the mini-player window."""
        super().show()
        # Ensure it stays on top
        self.raise_()
        self.activateWindow()

    def paintEvent(self, event):
        """Paint the window with rounded corners and drop shadow."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw drop shadow (simple implementation)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 50))
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 12, 12)

        # The actual content is drawn by child widgets
        super().paintEvent(event)

    def cleanup(self):
        """Clean up resources before closing."""
        # Disconnect all signals to prevent dangling connections
        try:
            self.player.state_changed.disconnect(self._on_player_state_changed)
            self.player.position_changed.disconnect(self.update_position)
            self.player.duration_changed.disconnect(self.update_duration)
            self.player.volume_changed.disconnect(self.update_volume_slider)
            self.player.track_changed.disconnect(self._on_track_changed)
        except (TypeError, RuntimeError):
            pass  # Already disconnected or signal doesn't exist

        # Stop any timers
        if hasattr(self, "auto_hide_timer"):
            self.auto_hide_timer.stop()

        # Clear references
        self.controller = None
        self.player = None

    def eventFilter(self, obj, event):
        if obj is self.title_bar:
            if event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    window = self.windowHandle()
                    if window is not None:
                        window.startSystemMove()
                    return True

        return super().eventFilter(obj, event)

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
        if artist_name and artist_name != "Unknown Artist":
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
        if performer_name and performer_name != "Unknown Artist":
            lines.append(f"Performed by {performer_name}")

        return "\n".join(lines)

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

    # Also update the _get_primary_artist_name to match the dock player version more closely:
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
