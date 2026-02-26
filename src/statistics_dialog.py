from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


class MusicStatsDialog(QDialog):
    def __init__(self, controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.stats = None  # Initialize stats as None
        self.setWindowTitle("Music Library Statistics")
        self.setMinimumSize(1000, 700)
        self.setup_ui()
        self.load_data()  # This will populate self.stats

        # Auto-refresh timer (optional)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.load_data)
        self.refresh_timer.start(30000)  # Refresh every 30 seconds

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Main tab widget
        self.tab_widget = QTabWidget()

        # Overview tab
        self.overview_tab = self.create_overview_tab()
        self.tab_widget.addTab(self.overview_tab, "Overview")

        # Artists & Albums tab
        self.artists_albums_tab = self.create_artists_albums_tab()
        self.tab_widget.addTab(self.artists_albums_tab, "Artists && Albums")

        # Genres & Moods tab
        self.genres_moods_tab = self.create_genres_moods_tab()
        self.tab_widget.addTab(self.genres_moods_tab, "Genres && Moods")

        # Audio Quality tab
        self.quality_tab = self.create_quality_tab()
        self.tab_widget.addTab(self.quality_tab, "Audio Quality")

        # Ratings tab
        self.ratings_tab = self.create_ratings_tab()
        self.tab_widget.addTab(self.ratings_tab, "Ratings")

        layout.addWidget(self.tab_widget)

    def create_overview_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Library Summary Group
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

        # Metadata completeness progress bar
        self.metadata_progress = QProgressBar()
        summary_layout.addWidget(QLabel("Completeness:"), 4, 0)
        summary_layout.addWidget(self.metadata_progress, 4, 1)

        layout.addWidget(summary_group)

        # Averages Group
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

        # Top Performers Group
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

        top_layout.addWidget(self.most_played_artist_label)
        top_layout.addWidget(self.highest_rated_artist_label)
        top_layout.addWidget(self.highest_rated_album_label)
        top_layout.addWidget(self.most_played_genre_label)
        top_layout.addWidget(self.highest_rated_genre_label)
        top_layout.addWidget(self.lowest_rated_genre_label)

        layout.addWidget(top_group)

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_artists_albums_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Split into two columns
        splitter = QSplitter(Qt.Horizontal)

        # Artists column
        artists_widget = QWidget()
        artists_layout = QVBoxLayout(artists_widget)

        artists_group = QGroupBox("Top 10 Artists by Plays")
        artists_group_layout = QVBoxLayout(artists_group)
        self.top_artists_labels = []
        for i in range(10):
            label = QLabel()
            self.top_artists_labels.append(label)
            artists_group_layout.addWidget(label)

        artists_layout.addWidget(artists_group)
        splitter.addWidget(artists_widget)

        # Albums column
        albums_widget = QWidget()
        albums_layout = QVBoxLayout(albums_widget)

        # Release years
        years_group = QGroupBox("Releases by Year")
        years_layout = QVBoxLayout(years_group)
        self.years_label = QLabel()
        self.years_label.setWordWrap(True)
        years_layout.addWidget(self.years_label)

        # Decades breakdown
        decades_group = QGroupBox("Library by Decade")
        decades_layout = QVBoxLayout(decades_group)
        self.decades_label = QLabel()
        self.decades_label.setWordWrap(True)
        decades_layout.addWidget(self.decades_label)

        albums_layout.addWidget(years_group)
        albums_layout.addWidget(decades_group)
        splitter.addWidget(albums_widget)

        splitter.setSizes([400, 400])
        layout.addWidget(splitter)

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_genres_moods_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        splitter = QSplitter(Qt.Horizontal)

        # Genres column
        genres_widget = QWidget()
        genres_layout = QVBoxLayout(genres_widget)

        genres_group = QGroupBox("Top 10 Genres by Plays")
        genres_group_layout = QVBoxLayout(genres_group)
        self.top_genres_labels = []
        for i in range(10):
            label = QLabel()
            self.top_genres_labels.append(label)
            genres_group_layout.addWidget(label)

        genres_layout.addWidget(genres_group)
        splitter.addWidget(genres_widget)

        # Moods column
        moods_widget = QWidget()
        moods_layout = QVBoxLayout(moods_widget)

        moods_group = QGroupBox("Top 10 Moods by Plays")
        moods_group_layout = QVBoxLayout(moods_group)
        self.top_moods_labels = []
        for i in range(10):
            label = QLabel()
            self.top_moods_labels.append(label)
            moods_group_layout.addWidget(label)

        moods_layout.addWidget(moods_group)
        splitter.addWidget(moods_widget)

        splitter.setSizes([400, 400])
        layout.addWidget(splitter)

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_quality_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Audio Quality Group
        quality_group = QGroupBox("Audio Quality Metrics")
        quality_layout = QGridLayout(quality_group)

        self.avg_bit_rate_label = self.create_stat_label("Average Bit Rate:")
        self.avg_bit_depth_label = self.create_stat_label("Average Bit Depth:")
        self.avg_file_size_label = self.create_stat_label("Average File Size:")
        self.total_track_length_label = self.create_stat_label("Total Track Length:")

        quality_layout.addWidget(self.avg_bit_rate_label, 0, 0)
        quality_layout.addWidget(self.avg_bit_depth_label, 0, 1)
        quality_layout.addWidget(self.avg_file_size_label, 1, 0)
        quality_layout.addWidget(self.total_track_length_label, 1, 1)

        layout.addWidget(quality_group)

        # File Format Distribution
        formats_group = QGroupBox("File Format Distribution")
        formats_layout = QVBoxLayout(formats_group)

        # Create labels for top file formats
        self.format_labels = []
        for i in range(10):  # Show top 10 formats
            label = QLabel()
            label.setStyleSheet("QLabel { padding: 2px; }")
            self.format_labels.append(label)
            formats_layout.addWidget(label)

        layout.addWidget(formats_group)

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_ratings_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Ratings Distribution
        ratings_group = QGroupBox("Ratings Distribution")
        ratings_layout = QVBoxLayout(ratings_group)
        self.ratings_labels = []
        for i in range(21):  # 0.0 to 10.0 in 0.5 increments
            label = QLabel()
            self.ratings_labels.append(label)
            ratings_layout.addWidget(label)

        layout.addWidget(ratings_group)

        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_stat_label(self, text):
        """Helper method to create consistent stat labels with styled values"""
        label = QLabel(text)
        label.setStyleSheet("""
            QLabel {
                padding: 2px;
            }
        """)
        return label

    def format_stat_value(self, value, is_numeric=True):
        """Format a statistic value with appropriate styling"""
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
        """Convert seconds to human readable format"""
        if not seconds:
            return "0s"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def format_file_size(self, bytes_size):
        """Convert bytes to human readable format"""
        if not bytes_size:
            return "0 B"

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} PB"

    def load_data(self):
        """Load all statistics data from the MusicStatistics utility"""
        try:
            # Get comprehensive statistics from the utility and store it
            self.stats = self.controller.statistics.get_comprehensive_statistics()

            self.load_overview_data()
            self.load_artists_albums_data()
            self.load_genres_moods_data()
            self.load_quality_data()
            self.load_ratings_data()
        except Exception as e:
            logger.error(f"Error loading statistics: {e}")

    def load_overview_data(self):
        """Load overview tab data from the statistics utility"""
        stats = self.stats

        # Library Summary - using HTML formatting for colored values
        total_tracks = self.format_stat_value(stats["total_tracks"])
        self.total_tracks_label.setText(f"Total Tracks: {total_tracks}")

        total_artists = self.format_stat_value(stats["total_artists"])
        self.total_artists_label.setText(f"Total Artists: {total_artists}")

        total_albums = self.format_stat_value(stats["total_albums"])
        self.total_albums_label.setText(f"Total Albums: {total_albums}")

        total_genres = self.format_stat_value(stats["total_genres"])
        self.total_genres_label.setText(f"Total Genres: {total_genres}")

        total_plays = self.format_stat_value(stats["total_plays"])
        self.total_plays_label.setText(f"Total Plays: {total_plays}")

        play_time = self.format_duration(stats["total_play_time"])
        self.total_play_time_label.setText(
            f"Total Play Time: <span style='color: #EA8599; font-weight: bold;'>{play_time}</span>"
        )

        file_size = self.format_file_size(stats["total_file_size"])
        self.total_file_size_label.setText(
            f"Total File Size: <span style='color: #EA8599; font-weight: bold;'>{file_size}</span>"
        )

        # Use metadata completeness from main stats
        overall_completeness = stats.get("overall_metadata_completeness", 0)
        completeness_value = self.format_stat_value(overall_completeness, False)
        self.metadata_completeness_label.setText(
            f"Metadata Complete: {completeness_value}%"
        )
        self.metadata_progress.setValue(int(overall_completeness))

        # Averages
        avg_tracks_per_artist = (
            stats["total_tracks"] / stats["total_artists"]
            if stats["total_artists"] > 0
            else 0
        )
        avg_tracks_artist_value = self.format_stat_value(avg_tracks_per_artist)
        self.avg_tracks_artist_label.setText(
            f"Tracks per Artist: {avg_tracks_artist_value}"
        )

        avg_tracks_per_genre = (
            stats["total_tracks"] / stats["total_genres"]
            if stats["total_genres"] > 0
            else 0
        )
        avg_tracks_genre_value = self.format_stat_value(avg_tracks_per_genre)
        self.avg_tracks_genre_label.setText(
            f"Tracks per Genre: {avg_tracks_genre_value}"
        )

        # Use avg_tracks_per_year from temporal statistics
        temporal_stats = stats.get("temporal_statistics", {})
        avg_tracks_per_year = temporal_stats.get("avg_tracks_per_year", "N/A")
        avg_tracks_year_value = self.format_stat_value(avg_tracks_per_year)
        self.avg_tracks_year_label.setText(f"Tracks per Year: {avg_tracks_year_value}")

        avg_rating = stats.get("average_rating", "No ratings")
        avg_rating_value = self.format_stat_value(avg_rating, False)
        self.avg_rating_label.setText(f"Average Rating: {avg_rating_value}")

        avg_played_rating = stats.get("average_played_rating", "No ratings")
        avg_played_rating_value = self.format_stat_value(avg_played_rating, False)
        self.avg_played_rating_label.setText(
            f"Avg Played Rating: {avg_played_rating_value}"
        )

        # Top Performers - use data from leaderboards
        leaderboards = stats.get("leaderboards", {})

        # Most played artist
        top_artists = leaderboards.get("top_artists", [])
        if top_artists:
            artist, plays = top_artists[0]
            artist_value = f"<span style='color: #EA8599; font-weight: bold;'>{artist.artist_name}</span>"
            self.most_played_artist_label.setText(f"Most Played Artist: {artist_value}")
        else:
            self.most_played_artist_label.setText("Most Played Artist: N/A")

        # Most played genre
        top_genres = leaderboards.get("top_genres", [])
        if top_genres:
            genre, plays = top_genres[0]
            genre_value = f"<span style='color: #EA8599; font-weight: bold;'>{genre.genre_name}</span>"
            self.most_played_genre_label.setText(f"Most Played Genre: {genre_value}")
        else:
            self.most_played_genre_label.setText("Most Played Genre: N/A")

        # Highest rated artist
        highest_rated_artists = leaderboards.get("highest_rated_artists", [])
        if highest_rated_artists:
            artist, avg_rating = highest_rated_artists[0]
            artist_value = f"<span style='color: #EA8599; font-weight: bold;'>{artist.artist_name} ({avg_rating:.1f})</span>"
            self.highest_rated_artist_label.setText(
                f"Highest Rated Artist: {artist_value}"
            )
        else:
            self.highest_rated_artist_label.setText("Highest Rated Artist: N/A")

        # Highest rated album
        highest_rated_albums = leaderboards.get("highest_rated_albums", [])
        if highest_rated_albums:
            album, avg_rating = highest_rated_albums[0]
            album_value = f"<span style='color: #EA8599; font-weight: bold;'>{album.album_name} ({avg_rating:.1f})</span>"
            self.highest_rated_album_label.setText(
                f"Highest Rated Album: {album_value}"
            )
        else:
            self.highest_rated_album_label.setText("Highest Rated Album: N/A")

        # Highest and lowest rated genres
        highest_rated_genres = leaderboards.get("highest_rated_genres", [])
        if highest_rated_genres:
            genre, avg_rating = highest_rated_genres[0]
            genre_value = f"<span style='color: #EA8599; font-weight: bold;'>{genre.genre_name} ({avg_rating:.1f})</span>"
            self.highest_rated_genre_label.setText(
                f"Highest Rated Genre: {genre_value}"
            )
        else:
            self.highest_rated_genre_label.setText("Highest Rated Genre: N/A")

        lowest_rated_genres = leaderboards.get("lowest_rated_genres", [])
        if lowest_rated_genres:
            genre, avg_rating = lowest_rated_genres[0]
            genre_value = f"<span style='color: #EA8599; font-weight: bold;'>{genre.genre_name} ({avg_rating:.1f})</span>"
            self.lowest_rated_genre_label.setText(f"Lowest Rated Genre: {genre_value}")
        else:
            self.lowest_rated_genre_label.setText("Lowest Rated Genre: N/A")

    def load_artists_albums_data(self):
        """Load artists and albums tab data from the statistics utility"""
        stats = self.stats
        leaderboards = stats.get("leaderboards", {})

        # Top Artists - consistent 2-value tuples: (object, value)
        top_artists = leaderboards.get("top_artists", [])
        for i, (artist, plays) in enumerate(top_artists):
            if i < len(self.top_artists_labels):
                self.top_artists_labels[i].setText(
                    f"{i + 1}. {artist.artist_name} - {plays:,} plays"
                )

        # Clear remaining labels
        for i in range(len(top_artists), len(self.top_artists_labels)):
            self.top_artists_labels[i].setText("")

        # Release years
        temporal_stats = stats.get("temporal_statistics", {})
        release_years = temporal_stats.get("release_years", {})
        if release_years:
            sorted_years = sorted(
                release_years.items(), key=lambda x: x[1], reverse=True
            )[:5]
            years_text = "\n".join(
                [f"{year}: {count} tracks" for year, count in sorted_years]
            )
            self.years_label.setText(years_text)
        else:
            self.years_label.setText("No release year data available")

        # Decades
        decades = temporal_stats.get("decade_breakdown", {})
        total_tracks = sum(decades.values()) if decades else 0
        if decades and total_tracks > 0:
            decade_text = "\n".join(
                [
                    f"{decade}: {count} tracks ({count / total_tracks * 100:.1f}%)"
                    for decade, count in sorted(decades.items())
                    if decade != "Unknown"
                ]
            )
            self.decades_label.setText(decade_text)
        else:
            self.decades_label.setText("No decade data available")

    def load_genres_moods_data(self):
        """Load genres and moods tab data from the statistics utility"""
        stats = self.stats
        leaderboards = stats.get("leaderboards", {})

        # Top Genres - consistent 2-value tuples: (object, value)
        top_genres = leaderboards.get("top_genres", [])
        for i, (genre, plays) in enumerate(top_genres):
            if i < len(self.top_genres_labels):
                self.top_genres_labels[i].setText(
                    f"{i + 1}. {genre.genre_name} - {plays:,} plays"
                )

        for i in range(len(top_genres), len(self.top_genres_labels)):
            self.top_genres_labels[i].setText("")

        # Top Moods - consistent 2-value tuples: (object, value)
        top_moods = leaderboards.get("top_moods", [])
        for i, (mood, plays) in enumerate(top_moods):
            if i < len(self.top_moods_labels):
                self.top_moods_labels[i].setText(
                    f"{i + 1}. {mood.mood_name} - {plays:,} plays"
                )

        for i in range(len(top_moods), len(self.top_moods_labels)):
            self.top_moods_labels[i].setText("")

    def load_quality_data(self):
        """Load audio quality tab data from the statistics utility"""
        stats = self.stats
        audio_stats = stats.get("audio_quality_stats", {})

        avg_bit_rate = audio_stats.get("average_bit_rate")
        bit_rate_value = (
            self.format_stat_value(avg_bit_rate, False) if avg_bit_rate else "N/A"
        )
        self.avg_bit_rate_label.setText(f"Average Bit Rate: {bit_rate_value} kbps")

        avg_bit_depth = audio_stats.get("average_bit_depth")
        bit_depth_value = (
            self.format_stat_value(avg_bit_depth, False) if avg_bit_depth else "N/A"
        )
        self.avg_bit_depth_label.setText(f"Average Bit Depth: {bit_depth_value} bits")

        avg_file_size = audio_stats.get("average_file_size")
        file_size_value = (
            self.format_file_size(avg_file_size) if avg_file_size else "N/A"
        )
        self.avg_file_size_label.setText(
            f"Average File Size: <span style='color: #EA8599; font-weight: bold;'>{file_size_value}</span>"
        )

        # Calculate total track length from average duration and total tracks
        avg_duration = audio_stats.get("average_duration", 0)
        total_tracks = stats.get("total_tracks", 0)
        total_length = avg_duration * total_tracks
        formatted_length = self.format_duration(total_length)
        self.total_track_length_label.setText(
            f"Total Track Length: <span style='color: #EA8599; font-weight: bold;'>{formatted_length}</span>"
        )

        # Load file format distribution
        self.load_file_format_data()

    def load_file_format_data(self):
        """Load file format distribution data"""
        try:
            # Get file format statistics from your controller
            format_stats = self.controller.statistics.get_file_format_distribution()

            # Clear existing labels
            for label in self.format_labels:
                label.setText("")

            # Check if we have format data
            if not format_stats:
                self.format_labels[0].setText("No file format data available")
                return

            # Display top formats
            sorted_formats = sorted(
                format_stats.items(), key=lambda x: x[1], reverse=True
            )[: len(self.format_labels)]

            for i, (format_name, count) in enumerate(sorted_formats):
                if i < len(self.format_labels):
                    percentage = (count / self.stats["total_tracks"]) * 100
                    format_value = self.format_stat_value(count)
                    self.format_labels[i].setText(
                        f"{format_name}: {format_value} tracks ({percentage:.1f}%)"
                    )

        except Exception as e:
            logger.error(f"Error loading file format data: {e}")
            self.format_labels[0].setText("Error loading file format data")

    def load_ratings_data(self):
        """Load ratings tab data from the statistics utility"""
        ratings_data = self.controller.statistics.get_ratings_distribution()

        # Extract the actual distribution dictionary
        distribution = ratings_data.get("distribution", {})

        # Sort by rating value (convert to float for proper numeric sorting)
        sorted_ratings = sorted(distribution.items(), key=lambda x: float(x[0]))

        for i, (rating, count) in enumerate(sorted_ratings):
            if i < len(self.ratings_labels):
                # Convert rating to stars (0-10 scale, showing half stars)
                rating_float = float(rating)
                full_stars = int(rating_float)
                half_star = "½" if rating_float % 1 != 0 else ""
                stars = "★" * full_stars + half_star

                self.ratings_labels[i].setText(f"{rating}/10 {stars}: {count} tracks")

        # Clear remaining labels
        for i in range(len(sorted_ratings), len(self.ratings_labels)):
            self.ratings_labels[i].setText("")

    def closeEvent(self, event):
        """Clean up when dialog is closed"""
        self.refresh_timer.stop()
        super().closeEvent(event)
