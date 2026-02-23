"""
NowPlayingView module.
"""

import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from asset_paths import asset
from logger_config import logger


class NowPlayingView(QWidget):
    """View to display current track info with controls."""

    def __init__(self, controller, track=None):
        """takes in controller for sql functions and a track object"""
        super().__init__()
        self.controller = controller
        self.track = track
        self.default_art_path = asset("default_album.svg")
        self.initUI()

    def initUI(self):
        """Initialize UI components."""

        # Main layout with proper sizing
        self.main_layout = QHBoxLayout()
        self.main_layout.setContentsMargins(20, 20, 20, 20)  # Reduced margins
        self.main_layout.setSpacing(20)
        self.setMinimumSize(800, 500)  # Reduced minimum size

        # Set size policy to allow shrinking
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Left Column - Album Art (now smaller)
        left_column = QVBoxLayout()
        left_column.setAlignment(Qt.AlignTop)

        # Smaller album art container
        self.album_art_container = QFrame()

        self.album_art_container.setFixedSize(300, 300)  # Smaller container

        album_art_layout = QVBoxLayout(self.album_art_container)
        album_art_layout.setAlignment(Qt.AlignCenter)

        self.album_art_label = QLabel()
        self.album_art_label.setFixedSize(260, 260)  # Smaller album art
        self.album_art_label.setAlignment(Qt.AlignCenter)
        album_art_layout.addWidget(self.album_art_label)
        left_column.addWidget(self.album_art_container)

        # Right Column - Track Info
        right_column = QVBoxLayout()
        right_column.setSpacing(15)

        # Allow right column to shrink
        right_column.setAlignment(Qt.AlignTop)

        # Track Title (smaller font)
        self.title_label = QLabel("No Track Playing")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setMinimumHeight(60)  # Smaller height
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_column.addWidget(self.title_label)

        # Album/Artist Info (smaller)
        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        self.details_label.setAlignment(Qt.AlignCenter)
        self.details_label.setMinimumHeight(50)  # Smaller height
        self.details_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_column.addWidget(self.details_label)

        # Collapsible sections container
        sections_container = QVBoxLayout()
        sections_container.setSpacing(8)

        # Allow sections container to expand but not too much
        sections_container.setAlignment(Qt.AlignTop)

        # Metadata Section
        self.metadata_frame = QFrame()
        self.metadata_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        metadata_layout = QVBoxLayout(self.metadata_frame)
        metadata_layout.setContentsMargins(12, 8, 12, 8)

        self.metadata_header = QLabel("Track Details ▼")
        self.metadata_header.mousePressEvent = self.toggle_metadata
        self.metadata_header.setCursor(Qt.PointingHandCursor)
        metadata_layout.addWidget(self.metadata_header)

        self.metadata_content = QLabel()
        self.metadata_content.setWordWrap(True)
        self.metadata_content.setAlignment(Qt.AlignTop)
        self.metadata_content.setVisible(False)
        self.metadata_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        metadata_layout.addWidget(self.metadata_content)

        sections_container.addWidget(self.metadata_frame)

        # Lyrics Section
        self.lyrics_frame = QFrame()
        self.lyrics_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lyrics_layout = QVBoxLayout(self.lyrics_frame)
        lyrics_layout.setContentsMargins(12, 8, 12, 8)

        self.lyrics_header = QLabel("Lyrics ▼")
        self.lyrics_header.mousePressEvent = self.toggle_lyrics
        self.lyrics_header.setCursor(Qt.PointingHandCursor)
        lyrics_layout.addWidget(self.lyrics_header)

        self.lyrics_content = QLabel()
        self.lyrics_content.setWordWrap(True)
        self.lyrics_content.setAlignment(Qt.AlignTop)
        self.lyrics_content.setVisible(False)
        # Limit lyrics height and make it scrollable if needed
        self.lyrics_content.setMaximumHeight(150)
        self.lyrics_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lyrics_layout.addWidget(self.lyrics_content)

        sections_container.addWidget(self.lyrics_frame)

        right_column.addLayout(sections_container)

        # Combine main layout with stretch factors
        self.main_layout.addLayout(left_column)
        self.main_layout.addLayout(right_column)

        # Set stretch factors - right column gets more space
        self.main_layout.setStretchFactor(left_column, 1)
        self.main_layout.setStretchFactor(right_column, 2)

        self.setLayout(self.main_layout)

        # Update UI if track exists
        if self.track:
            self.updateUI(self.track)
        else:
            self.clearUI()

    def updateUI(self, track):
        """Update all components with track data."""
        try:
            logger.info(
                f"NowPlayingView.updateUI called with track: {getattr(track, 'track_name', 'Unknown')}"
            )

            if not track:
                logger.warning("No track provided to updateUI")
                self.clearUI()
                return

            # Basic Info
            title = getattr(track, "track_name", "Unknown Title")
            logger.info(f"Setting title: {title}")
            self.title_label.setText(title)

            # Build details text safely
            details = []

            if hasattr(track, "album") and track.album:
                album_name = getattr(track.album, "album_name", None)
                if album_name:
                    details.append(f"🎵 {album_name}")

            if hasattr(track, "artists") and track.artists:
                if len(track.artists) > 0:
                    artist_name = getattr(track.artists[0], "artist_name", None)
                    if artist_name:
                        details.append(f"🎤 {artist_name}")

            details_text = (
                " • ".join(details) if details else "No album/artist information"
            )
            self.details_label.setText(details_text)

            # Lyrics
            lyrics = getattr(track, "lyrics", None)
            lyrics_text = lyrics if lyrics else "🎵 Lyrics not available 🎵"
            self.lyrics_content.setText(lyrics_text)

            # Show/hide lyrics section based on content
            has_lyrics = bool(
                lyrics
                and lyrics.strip()
                and lyrics not in ["Lyrics not available", "🎵 Lyrics not available 🎵"]
            )
            self.lyrics_frame.setVisible(has_lyrics)

            # Detailed Metadata
            metadata_lines = self._build_detailed_metadata(track)
            self.metadata_content.setText("\n".join(metadata_lines))

            # Show metadata section only if we have data
            has_metadata = len(metadata_lines) > 1
            self.metadata_frame.setVisible(has_metadata)

            # Album Art
            art_path = None
            if hasattr(track, "album") and track.album:
                art_path_str = getattr(track.album, "front_cover_path", "")
                if art_path_str:
                    art_path = Path(art_path_str)

            if art_path and art_path.exists():
                logger.info(f"Loading album art from: {art_path}")
                pixmap = QPixmap(str(art_path))
                # Create rounded pixmap
                rounded_pixmap = self.create_rounded_pixmap(pixmap, 350)
                self.album_art_label.setPixmap(rounded_pixmap)
            else:
                logger.warning("Album art not found, using default")
                if self.default_art_path and Path(self.default_art_path).exists():
                    pixmap = QPixmap(self.default_art_path)
                    rounded_pixmap = self.create_rounded_pixmap(pixmap, 350)
                    self.album_art_label.setPixmap(rounded_pixmap)
                else:
                    self.album_art_label.setText("🎵\nNo Album Art")

            logger.info("UI update completed successfully")

        except Exception as e:
            logger.error(f"UI update failed: {e}")

            logger.error(traceback.format_exc())
            self.clearUI()

    def create_rounded_pixmap(self, pixmap, size):
        """Create a rounded pixmap for album art."""
        # Use the provided size instead of hardcoded 350
        scaled_pixmap = pixmap.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        # Create transparent pixmap for the result
        result = QPixmap(size, size)
        result.fill(Qt.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        # Create rounded clip path
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 12, 12)  # Smaller border radius
        painter.setClipPath(path)

        # Center the pixmap
        x = (size - scaled_pixmap.width()) // 2
        y = (size - scaled_pixmap.height()) // 2
        painter.drawPixmap(x, y, scaled_pixmap)

        painter.end()
        return result

    def _build_detailed_metadata(self, track):
        """Build detailed metadata string from track object."""
        metadata = []

        # Basic track info
        if hasattr(track, "duration") and track.duration:
            minutes = track.duration // 60
            seconds = track.duration % 60
            metadata.append(f"⏱️ Duration: {int(minutes)}:{int(seconds):02d}")

        if hasattr(track, "track_number") and track.track_number:
            metadata.append(f"🔢 Track: {track.track_number}")

        # Audio technical info
        if hasattr(track, "bit_rate") and track.bit_rate:
            metadata.append(f"📊 Bit Rate: {track.bit_rate} kbps")

        if hasattr(track, "sample_rate") and track.sample_rate:
            metadata.append(f"🎚️  Sample Rate: {track.sample_rate} Hz")

        # Music analysis
        if hasattr(track, "bpm") and track.bpm:
            metadata.append(f"💓 BPM: {track.bpm}")

        if hasattr(track, "key") and track.key:
            mode = getattr(track, "mode", "")
            key_text = f"🎹 Key: {track.key} {mode}" if mode else f"🎹 Key: {track.key}"
            metadata.append(key_text)

        # Additional metadata
        if hasattr(track, "play_count") and track.play_count:
            metadata.append(f"📈 Play Count: {track.play_count}")

        if hasattr(track, "user_rating") and track.user_rating:
            stars = "⭐" * int(track.user_rating)
            metadata.append(f"⭐ Rating: {stars}")

        return metadata if metadata else ["No detailed metadata available"]

    def toggle_lyrics(self, event):
        """Toggle lyrics section visibility."""
        is_visible = not self.lyrics_content.isVisible()
        self.lyrics_content.setVisible(is_visible)
        self.lyrics_header.setText("Lyrics ▲" if is_visible else "Lyrics ▼")

    def toggle_metadata(self, event):
        """Toggle metadata section visibility."""
        is_visible = not self.metadata_content.isVisible()
        self.metadata_content.setVisible(is_visible)
        self.metadata_header.setText(
            "Track Details ▲" if is_visible else "Track Details ▼"
        )

    def clearUI(self):
        """Reset UI to default state."""
        self.title_label.setText("No Track Playing")
        self.details_label.setText("Select a track to begin")
        self.lyrics_content.clear()
        self.metadata_content.clear()

        # Collapse both sections
        self.lyrics_content.setVisible(False)
        self.lyrics_header.setText("Lyrics ▼")
        self.metadata_content.setVisible(False)
        self.metadata_header.setText("Track Details ▼")

        # Load default art if available
        if self.default_art_path and Path(self.default_art_path).exists():
            pixmap = QPixmap(self.default_art_path)
            rounded_pixmap = self.create_rounded_pixmap(pixmap, 350)
            self.album_art_label.setPixmap(rounded_pixmap)
        else:
            self.album_art_label.clear()
            self.album_art_label.setText("🎵\nNo Album Art")
