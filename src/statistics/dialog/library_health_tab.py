"""Library Health tab: metadata completeness, ratings distribution, chart-year, file formats."""

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.foundation.logger_config import logger
from src.statistics.dialog.shared import _HIGHLIGHT_COLOR, _match_detail
from src.statistics.widgets.rating_distribution_chart import RatingDistributionChart
from src.statistics.widgets.stat_tile import StatTileWidget


class LibraryHealthTabMixin:
    def create_library_health_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Metadata completeness — 4 progress bars + overall
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

        completeness_layout.addWidget(QLabel("Publishers:"), 3, 0)
        self.publishers_completeness_progress = QProgressBar()
        self.publishers_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.publishers_completeness_progress, 3, 1)
        completeness_layout.addWidget(self.publishers_completeness_label, 3, 2)

        completeness_layout.addWidget(QLabel("Overall:"), 4, 0)
        self.overall_completeness_progress = QProgressBar()
        self.overall_completeness_label = QLabel("0%")
        completeness_layout.addWidget(self.overall_completeness_progress, 4, 1)
        completeness_layout.addWidget(self.overall_completeness_label, 4, 2)

        layout.addWidget(completeness_group)

        # Ratings distribution
        ratings_group = QGroupBox("Ratings Distribution (0.5 – 10)")  # noqa: RUF001
        ratings_layout = QVBoxLayout(ratings_group)

        self.ratings_summary_label = self.create_stat_label("")
        ratings_layout.addWidget(self.ratings_summary_label)

        self.ratings_chart = RatingDistributionChart()
        ratings_layout.addWidget(self.ratings_chart)

        layout.addWidget(ratings_group)

        chart_year_group = QGroupBox("Most Complete Chart Year")
        chart_year_layout = QHBoxLayout(chart_year_group)
        self.chart_year_track_tile = StatTileWidget("Track Charts")
        self.chart_year_album_tile = StatTileWidget("Album Charts")
        chart_year_layout.addWidget(self.chart_year_track_tile)
        chart_year_layout.addWidget(self.chart_year_album_tile)
        layout.addWidget(chart_year_group)

        # File Formats
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

    def load_library_health_data(self):
        """Load library health tab data (completeness, ratings, formats)."""
        stats = self.stats
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

        publishers_pct = completeness.get("publishers_complete", 0)
        self.publishers_completeness_progress.setValue(int(publishers_pct))
        self.publishers_completeness_label.setText(f"{publishers_pct:.1f}%")

        overall = stats.get("overall_metadata_completeness", 0)
        total_pct = completeness.get("total_complete", overall)
        self.overall_completeness_progress.setValue(int(total_pct))
        self.overall_completeness_label.setText(f"{total_pct:.1f}%")

        self.load_ratings_data()
        self.load_file_format_data()
        self.load_chart_year_data()

    def load_chart_year_data(self):
        """Load most-complete-chart-year data (already fetched in self.stats)."""
        chart_years = self.stats.get("most_complete_chart_year", {})

        track_year = chart_years.get("Track")
        if track_year:
            self.chart_year_track_tile.set_data(track_year["year"], _match_detail(track_year))
        else:
            self.chart_year_track_tile.set_data("N/A")

        album_year = chart_years.get("Album")
        if album_year:
            self.chart_year_album_tile.set_data(album_year["year"], _match_detail(album_year))
        else:
            self.chart_year_album_tile.set_data("N/A")

    def load_ratings_data(self):
        """Load ratings distribution data (already fetched in self.stats)."""
        ratings_data = self.stats.get("ratings_distribution", {})
        distribution = {float(k): v for k, v in ratings_data.get("distribution", {}).items()}

        total_rated = ratings_data.get("total_rated", sum(distribution.values()))
        total_unrated = ratings_data.get("total_unrated", 0)
        total_invalid = ratings_data.get("total_invalid_rating", 0)

        summary_text = (
            f"Rated: {self.format_stat_value(total_rated)}  •  "
            f"Unrated: {self.format_stat_value(total_unrated)}"
        )
        if total_invalid:
            summary_text += (
                f"  •  <span style='color: {_HIGHLIGHT_COLOR};'>"
                f"{self.format_stat_value(total_invalid, False)} tracks have "
                f"invalid ratings</span>"
            )
        self.ratings_summary_label.setText(summary_text)

        self.ratings_chart.set_data(distribution)

    def load_file_format_data(self):
        """Load file format distribution data (already fetched in self.stats)."""
        try:
            format_stats = self.stats.get("file_format_distribution", {})

            for label in self.format_labels:
                label.setText("")

            if not format_stats:
                self.format_labels[0].setText("No file format data available")
                return

            sorted_formats = sorted(format_stats.items(), key=lambda x: x[1], reverse=True)[
                : len(self.format_labels)
            ]

            total_tracks = self.stats["total_tracks"]
            for i, (format_name, count) in enumerate(sorted_formats):
                if i < len(self.format_labels):
                    percentage = (count / total_tracks * 100) if total_tracks else 0
                    self.format_labels[i].setText(
                        f"{format_name}: {self.format_stat_value(count)} tracks ({percentage:.1f}%)"
                    )
        except (KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error loading file format data: {e}")
            self.format_labels[0].setText("Error loading file format data")
