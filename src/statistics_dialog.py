from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


class MusicStatsDialog(QDialog):
    def __init__(self, controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.stats = None
        self.setWindowTitle("Music Library Statistics")
        self.setMinimumSize(1000, 700)
        self.setup_ui()
        self.load_data()

        # Auto-refresh every 30 seconds
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.load_data)
        self.refresh_timer.start(30000)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget()

        self.overview_tab = self.create_overview_tab()
        self.tab_widget.addTab(self.overview_tab, "Overview")

        self.artists_albums_tab = self.create_artists_albums_tab()
        self.tab_widget.addTab(self.artists_albums_tab, "Artists && Albums")

        self.genres_moods_tab = self.create_genres_moods_tab()
        self.tab_widget.addTab(self.genres_moods_tab, "Genres && Moods")

        self.quality_tab = self.create_quality_tab()
        self.tab_widget.addTab(self.quality_tab, "Audio Quality")

        self.ratings_tab = self.create_ratings_tab()
        self.tab_widget.addTab(self.ratings_tab, "Ratings")

        layout.addWidget(self.tab_widget)

    # ------------------------------------------------------------------ #
    #  Tab builders                                                        #
    # ------------------------------------------------------------------ #

    def create_overview_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Library Summary
        summary_group = QGroupBox("Library Summary")
        summary_layout = QGridLayout(summary_group)

        self.total_tracks_label = self.create_stat_label("Total Tracks:")
        self.total_artists_label = self.create_stat_label("Total Artists:")
        self.total_albums_label = self.create_stat_label("Total Albums:")
        self.total_genres_label = self.create_stat_label("Total Genres:")
        self.total_plays_label = self.create_stat_label("Total Plays:")
        self.total_play_time_label = self.create_stat_label("Total Play Time:")
        self.total_file_size_label = self.create_stat_label("Total File Size:")
        self.metadata_completeness_label = self.create_stat_label("Metadata Complete:")

        summary_layout.addWidget(self.total_tracks_label, 0, 0)
        summary_layout.addWidget(self.total_artists_label, 0, 1)
        summary_layout.addWidget(self.total_albums_label, 1, 0)
        summary_layout.addWidget(self.total_genres_label, 1, 1)
        summary_layout.addWidget(self.total_plays_label, 2, 0)
        summary_layout.addWidget(self.total_play_time_label, 2, 1)
        summary_layout.addWidget(self.total_file_size_label, 3, 0)
        summary_layout.addWidget(self.metadata_completeness_label, 3, 1)

        layout.addWidget(summary_group)

        # Metadata completeness — 4 progress bars
        completeness_group = QGroupBox("Metadata Completeness")
        completeness_layout = QGridLayout(completeness_group)

        completeness_layout.addWidget(QLabel("Tracks:"), 0, 0)
        self.tracks_completeness_progress = QProgressBar()
        self.tracks_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.tracks_completeness_progress, 0, 1)
        completeness_layout.addWidget(self.tracks_completeness_label, 0, 2)

        completeness_layout.addWidget(QLabel("Artists:"), 1, 0)
        self.artists_completeness_progress = QProgressBar()
        self.artists_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.artists_completeness_progress, 1, 1)
        completeness_layout.addWidget(self.artists_completeness_label, 1, 2)

        completeness_layout.addWidget(QLabel("Albums:"), 2, 0)
        self.albums_completeness_progress = QProgressBar()
        self.albums_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.albums_completeness_progress, 2, 1)
        completeness_layout.addWidget(self.albums_completeness_label, 2, 2)

        completeness_layout.addWidget(QLabel("Overall:"), 3, 0)
        self.overall_completeness_progress = QProgressBar()
        self.overall_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.overall_completeness_progress, 3, 1)
        completeness_layout.addWidget(self.overall_completeness_label, 3, 2)

        layout.addWidget(completeness_group)

        # Averages
        averages_group = QGroupBox("Averages")
        averages_layout = QGridLayout(averages_group)

        self.avg_tracks_artist_label = self.create_stat_label("Tracks per Artist:")
        self.avg_tracks_year_label = self.create_stat_label("Tracks per Year:")
        self.avg_tracks_genre_label = self.create_stat_label("Tracks per Genre:")
        self.avg_rating_label = self.create_stat_label("Average Rating:")
        self.avg_played_rating_label = self.create_stat_label("Avg Played Rating:")

        averages_layout.addWidget(self.avg_tracks_artist_label, 0, 0)
        averages_layout.addWidget(self.avg_tracks_year_label, 0, 1)
        averages_layout.addWidget(self.avg_tracks_genre_label, 1, 0)
        averages_layout.addWidget(self.avg_rating_label, 1, 1)
        averages_layout.addWidget(self.avg_played_rating_label, 2, 0)

        layout.addWidget(averages_group)

        # Top Performers
        top_group = QGroupBox("Top Performers")
        top_layout = QVBoxLayout(top_group)

        self.most_played_artist_label = self.create_stat_label("Most Played Artist:")
        self.highest_rated_artist_label = self.create_stat_label(
            "Highest Rated Artist:"
        )
        self.highest_rated_album_label = self.create_stat_label("Highest Rated Album:")
        self.most_played_genre_label = self.create_stat_label("Most Played Genre:")
        self.highest_rated_genre_label = self.create_stat_label("Highest Rated Genre:")
        self.lowest_rated_genre_label = self.create_stat_label("Lowest Rated Genre:")

        for lbl in [
            self.most_played_artist_label,
            self.highest_rated_artist_label,
            self.highest_rated_album_label,
            self.most_played_genre_label,
            self.highest_rated_genre_label,
            self.lowest_rated_genre_label,
        ]:
            top_layout.addWidget(lbl)

        layout.addWidget(top_group)
        layout.addStretch()

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_artists_albums_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Top Artists
        artists_group = QGroupBox("Top Artists by Plays")
        artists_layout = QVBoxLayout(artists_group)
        self.top_artists_labels = []
        for _ in range(10):
            label = QLabel()
            self.top_artists_labels.append(label)
            artists_layout.addWidget(label)
        layout.addWidget(artists_group)

        # Top Albums
        albums_group = QGroupBox("Highest Rated Albums")
        albums_layout = QVBoxLayout(albums_group)
        self.top_albums_labels = []
        for _ in range(5):
            label = QLabel()
            self.top_albums_labels.append(label)
            albums_layout.addWidget(label)
        layout.addWidget(albums_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_genres_moods_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        genres_group = QGroupBox("Top Genres by Plays")
        genres_layout = QVBoxLayout(genres_group)
        self.top_genres_labels = []
        for _ in range(10):
            label = QLabel()
            self.top_genres_labels.append(label)
            genres_layout.addWidget(label)
        layout.addWidget(genres_group)

        moods_group = QGroupBox("Top Moods by Plays")
        moods_layout = QVBoxLayout(moods_group)
        self.top_moods_labels = []
        for _ in range(10):
            label = QLabel()
            self.top_moods_labels.append(label)
            moods_layout.addWidget(label)
        layout.addWidget(moods_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_quality_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        quality_group = QGroupBox("Audio Quality")
        quality_layout = QVBoxLayout(quality_group)

        self.avg_bit_rate_label = self.create_stat_label("Average Bit Rate:")
        self.avg_bit_depth_label = self.create_stat_label("Average Bit Depth:")
        self.avg_file_size_label = self.create_stat_label("Average File Size:")
        self.total_track_length_label = self.create_stat_label("Total Track Length:")

        for lbl in [
            self.avg_bit_rate_label,
            self.avg_bit_depth_label,
            self.avg_file_size_label,
            self.total_track_length_label,
        ]:
            quality_layout.addWidget(lbl)

        layout.addWidget(quality_group)

        formats_group = QGroupBox("File Formats")
        formats_layout = QVBoxLayout(formats_group)
        self.format_labels = []
        for _ in range(15):
            label = QLabel()
            self.format_labels.append(label)
            formats_layout.addWidget(label)
        layout.addWidget(formats_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_ratings_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        ratings_group = QGroupBox("Ratings Distribution (0.5 – 10)")
        ratings_layout = QVBoxLayout(ratings_group)
        self.ratings_labels = []
        for _ in range(21):  # 0.5 to 10.0 in 0.5 increments = 20 slots + header
            label = QLabel()
            self.ratings_labels.append(label)
            ratings_layout.addWidget(label)
        layout.addWidget(ratings_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    def load_data(self):
        """Load all statistics data from the MusicStatistics utility."""
        try:
            self.stats = self.controller.statistics.get_comprehensive_statistics()
            self.load_overview_data()
            self.load_artists_albums_data()
            self.load_genres_moods_data()
            self.load_quality_data()
            self.load_ratings_data()
        except Exception as e:
            logger.error(f"Error loading statistics: {e}")

    def load_overview_data(self):
        """Load overview tab data."""
        stats = self.stats

        self.total_tracks_label.setText(
            f"Total Tracks: {self.format_stat_value(stats['total_tracks'])}"
        )
        self.total_artists_label.setText(
            f"Total Artists: {self.format_stat_value(stats['total_artists'])}"
        )
        self.total_albums_label.setText(
            f"Total Albums: {self.format_stat_value(stats['total_albums'])}"
        )
        self.total_genres_label.setText(
            f"Total Genres: {self.format_stat_value(stats['total_genres'])}"
        )
        self.total_plays_label.setText(
            f"Total Plays: {self.format_stat_value(stats['total_plays'])}"
        )

        play_time = self.format_duration(stats["total_play_time"])
        self.total_play_time_label.setText(
            f"Total Play Time: <span style='color: #EA8599; font-weight: bold;'>{play_time}</span>"
        )

        file_size = self.format_file_size(stats["total_file_size"])
        self.total_file_size_label.setText(
            f"Total File Size: <span style='color: #EA8599; font-weight: bold;'>{file_size}</span>"
        )

        # Overall completeness summary label
        overall = stats.get("overall_metadata_completeness", 0)
        self.metadata_completeness_label.setText(
            f"Metadata Complete: {self.format_stat_value(overall, False)}%"
        )

        # 4-axis completeness progress bars
        completeness = stats.get("metadata_completeness", {})

        tracks_pct = completeness.get("tracks_complete", 0)
        self.tracks_completeness_progress.setValue(int(tracks_pct))
        self.tracks_completeness_label.setText(f"{tracks_pct:.1f}%")

        artists_pct = completeness.get("artists_complete", 0)
        self.artists_completeness_progress.setValue(int(artists_pct))
        self.artists_completeness_label.setText(f"{artists_pct:.1f}%")

        albums_pct = completeness.get("albums_complete", 0)
        self.albums_completeness_progress.setValue(int(albums_pct))
        self.albums_completeness_label.setText(f"{albums_pct:.1f}%")

        total_pct = completeness.get("total_complete", overall)
        self.overall_completeness_progress.setValue(int(total_pct))
        self.overall_completeness_label.setText(f"{total_pct:.1f}%")

        # Averages
        avg_tracks_per_artist = (
            stats["total_tracks"] / stats["total_artists"]
            if stats["total_artists"] > 0
            else 0
        )
        self.avg_tracks_artist_label.setText(
            f"Tracks per Artist: {self.format_stat_value(avg_tracks_per_artist)}"
        )

        avg_tracks_per_genre = (
            stats["total_tracks"] / stats["total_genres"]
            if stats["total_genres"] > 0
            else 0
        )
        self.avg_tracks_genre_label.setText(
            f"Tracks per Genre: {self.format_stat_value(avg_tracks_per_genre)}"
        )

        temporal_stats = stats.get("temporal_statistics", {})
        avg_tracks_per_year = temporal_stats.get("avg_tracks_per_year", "N/A")
        self.avg_tracks_year_label.setText(
            f"Tracks per Year: {self.format_stat_value(avg_tracks_per_year)}"
        )

        avg_rating = stats.get("average_rating", "No ratings")
        self.avg_rating_label.setText(
            f"Average Rating: {self.format_stat_value(avg_rating, False)}"
        )

        avg_played_rating = stats.get("average_played_rating", "No ratings")
        self.avg_played_rating_label.setText(
            f"Avg Played Rating: {self.format_stat_value(avg_played_rating, False)}"
        )

        # Top Performers — leaderboard entries are now plain (name, value) tuples
        leaderboards = stats.get("leaderboards", {})

        top_artists = leaderboards.get("top_artists", [])
        if top_artists:
            name, _ = top_artists[0]
            self.most_played_artist_label.setText(
                f"Most Played Artist: <span style='color: #EA8599; font-weight: bold;'>{name}</span>"
            )
        else:
            self.most_played_artist_label.setText("Most Played Artist: N/A")

        top_genres = leaderboards.get("top_genres", [])
        if top_genres:
            name, _ = top_genres[0]
            self.most_played_genre_label.setText(
                f"Most Played Genre: <span style='color: #EA8599; font-weight: bold;'>{name}</span>"
            )
        else:
            self.most_played_genre_label.setText("Most Played Genre: N/A")

        highest_rated_artists = leaderboards.get("highest_rated_artists", [])
        if highest_rated_artists:
            name, avg_rating = highest_rated_artists[0]
            self.highest_rated_artist_label.setText(
                f"Highest Rated Artist: <span style='color: #EA8599; font-weight: bold;'>{name} ({avg_rating:.1f})</span>"
            )
        else:
            self.highest_rated_artist_label.setText("Highest Rated Artist: N/A")

        highest_rated_albums = leaderboards.get("highest_rated_albums", [])
        if highest_rated_albums:
            name, avg_rating = highest_rated_albums[0]
            self.highest_rated_album_label.setText(
                f"Highest Rated Album: <span style='color: #EA8599; font-weight: bold;'>{name} ({avg_rating:.1f})</span>"
            )
        else:
            self.highest_rated_album_label.setText("Highest Rated Album: N/A")

        highest_rated_genres = leaderboards.get("highest_rated_genres", [])
        if highest_rated_genres:
            name, avg_rating = highest_rated_genres[0]
            self.highest_rated_genre_label.setText(
                f"Highest Rated Genre: <span style='color: #EA8599; font-weight: bold;'>{name} ({avg_rating:.1f})</span>"
            )
        else:
            self.highest_rated_genre_label.setText("Highest Rated Genre: N/A")

        lowest_rated_genres = leaderboards.get("lowest_rated_genres", [])
        if lowest_rated_genres:
            name, avg_rating = lowest_rated_genres[0]
            self.lowest_rated_genre_label.setText(
                f"Lowest Rated Genre: <span style='color: #EA8599; font-weight: bold;'>{name} ({avg_rating:.1f})</span>"
            )
        else:
            self.lowest_rated_genre_label.setText("Lowest Rated Genre: N/A")

    def load_artists_albums_data(self):
        """Load artists and albums tab data."""
        leaderboards = self.stats.get("leaderboards", {})

        top_artists = leaderboards.get("top_artists", [])
        for i, (name, plays) in enumerate(top_artists):
            if i < len(self.top_artists_labels):
                self.top_artists_labels[i].setText(f"{i + 1}. {name} — {plays:,} plays")
        for i in range(len(top_artists), len(self.top_artists_labels)):
            self.top_artists_labels[i].setText("")

        top_albums = leaderboards.get("highest_rated_albums", [])
        for i, (name, avg_rating) in enumerate(top_albums):
            if i < len(self.top_albums_labels):
                self.top_albums_labels[i].setText(
                    f"{i + 1}. {name} — avg {avg_rating:.1f}"
                )
        for i in range(len(top_albums), len(self.top_albums_labels)):
            self.top_albums_labels[i].setText("")

    def load_genres_moods_data(self):
        """Load genres and moods tab data."""
        leaderboards = self.stats.get("leaderboards", {})

        top_genres = leaderboards.get("top_genres", [])
        for i, (name, plays) in enumerate(top_genres):
            if i < len(self.top_genres_labels):
                self.top_genres_labels[i].setText(f"{i + 1}. {name} — {plays:,} plays")
        for i in range(len(top_genres), len(self.top_genres_labels)):
            self.top_genres_labels[i].setText("")

        top_moods = leaderboards.get("top_moods", [])
        for i, (name, plays) in enumerate(top_moods):
            if i < len(self.top_moods_labels):
                self.top_moods_labels[i].setText(f"{i + 1}. {name} — {plays:,} plays")
        for i in range(len(top_moods), len(self.top_moods_labels)):
            self.top_moods_labels[i].setText("")

    def load_quality_data(self):
        """Load audio quality tab data."""
        stats = self.stats
        audio_stats = stats.get("audio_quality_stats", {})

        avg_bit_rate = audio_stats.get("average_bit_rate")
        self.avg_bit_rate_label.setText(
            f"Average Bit Rate: {self.format_stat_value(avg_bit_rate, False) if avg_bit_rate else 'N/A'} kbps"
        )

        avg_bit_depth = audio_stats.get("average_bit_depth")
        self.avg_bit_depth_label.setText(
            f"Average Bit Depth: {self.format_stat_value(avg_bit_depth, False) if avg_bit_depth else 'N/A'} bits"
        )

        avg_file_size = audio_stats.get("average_file_size")
        file_size_value = (
            self.format_file_size(avg_file_size) if avg_file_size else "N/A"
        )
        self.avg_file_size_label.setText(
            f"Average File Size: <span style='color: #EA8599; font-weight: bold;'>{file_size_value}</span>"
        )

        # Total track length = average duration × total tracks
        avg_duration = audio_stats.get("average_duration", 0)
        total_tracks = stats.get("total_tracks", 0)
        total_length = (avg_duration or 0) * total_tracks
        formatted_length = self.format_duration(total_length)
        self.total_track_length_label.setText(
            f"Total Track Length: <span style='color: #EA8599; font-weight: bold;'>{formatted_length}</span>"
        )

        self.load_file_format_data()

    def load_file_format_data(self):
        """Load file format distribution data."""
        try:
            format_stats = self.controller.statistics.get_file_format_distribution()

            for label in self.format_labels:
                label.setText("")

            if not format_stats:
                self.format_labels[0].setText("No file format data available")
                return

            sorted_formats = sorted(
                format_stats.items(), key=lambda x: x[1], reverse=True
            )[: len(self.format_labels)]

            total_tracks = self.stats["total_tracks"]
            for i, (format_name, count) in enumerate(sorted_formats):
                if i < len(self.format_labels):
                    percentage = (count / total_tracks * 100) if total_tracks else 0
                    self.format_labels[i].setText(
                        f"{format_name}: {self.format_stat_value(count)} tracks ({percentage:.1f}%)"
                    )
        except Exception as e:
            logger.error(f"Error loading file format data: {e}")
            self.format_labels[0].setText("Error loading file format data")

    def load_ratings_data(self):
        """Load ratings tab data."""
        ratings_data = self.controller.statistics.get_ratings_distribution()
        distribution = ratings_data.get("distribution", {})

        sorted_ratings = sorted(distribution.items(), key=lambda x: float(x[0]))

        for i, (rating, count) in enumerate(sorted_ratings):
            if i < len(self.ratings_labels):
                rating_float = float(rating)
                full_stars = int(rating_float)
                half_star = "½" if rating_float % 1 != 0 else ""
                stars = "★" * full_stars + half_star
                self.ratings_labels[i].setText(f"{rating}/10 {stars}: {count} tracks")

        for i in range(len(sorted_ratings), len(self.ratings_labels)):
            self.ratings_labels[i].setText("")

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def create_stat_label(self, text):
        """Create a consistent stat label."""
        label = QLabel(text)
        label.setStyleSheet("QLabel { padding: 2px; }")
        return label

    def format_stat_value(self, value, is_numeric=True):
        """Format a statistic value with colour styling."""
        if value is None or value == "N/A":
            formatted_value = "N/A"
        elif is_numeric and isinstance(value, (int, float)):
            formatted_value = f"{value:,}" if isinstance(value, int) else f"{value:.1f}"
        else:
            formatted_value = str(value)

        return (
            f'<span style="color: #EA8599; font-weight: bold;">{formatted_value}</span>'
        )

    def format_duration(self, seconds):
        """Convert a duration in seconds to a human-readable string.

        Scales automatically:
          - Under 1 minute  → "Xs"
          - Under 1 hour    → "Xm Ys"
          - Under 1 day     → "Xh Ym"
          - Under 1 year    → "Xd Yh"
          - 1 year or more  → "Xy Zd"
        """
        if not seconds:
            return "0s"

        seconds = int(seconds)

        MINUTE = 60
        HOUR = 3600
        DAY = 86400
        YEAR = 365 * DAY

        if seconds < MINUTE:
            return f"{seconds}s"
        elif seconds < HOUR:
            m = seconds // MINUTE
            s = seconds % MINUTE
            return f"{m}m {s}s"
        elif seconds < DAY:
            h = seconds // HOUR
            m = (seconds % HOUR) // MINUTE
            return f"{h}h {m}m"
        elif seconds < YEAR:
            d = seconds // DAY
            h = (seconds % DAY) // HOUR
            return f"{d}d {h}h"
        else:
            y = seconds // YEAR
            d = (seconds % YEAR) // DAY
            return f"{y}y {d}d"

    def format_file_size(self, bytes_size):
        """Convert bytes to a human-readable string."""
        if not bytes_size:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} PB"

    def closeEvent(self, event):
        """Stop the refresh timer when the dialog is closed."""
        self.refresh_timer.stop()
        super().closeEvent(event)
