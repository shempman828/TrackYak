import contextlib
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger
from src.statistics.album_stats_worker import AlbumStatsWorker
from src.statistics.artist_stats_worker import ArtistStatsWorker
from src.statistics.audio_stats_worker import AudioStatsWorker
from src.statistics.charts.bar_distribution_chart import BarDistributionChart
from src.statistics.charts.histogram_chart import HistogramChart
from src.statistics.charts.leaderboard_list import LeaderboardListWidget
from src.statistics.charts.stat_tile import StatTileWidget
from src.statistics.charts.threshold_tier_widget import ThresholdTierWidget
from src.statistics.charts.word_cloud_widget import WordCloudWidget
from src.statistics.charts.year_time_series_chart import YearTimeSeriesChart
from src.statistics.genre_mood_stats_worker import GenreMoodStatsWorker
from src.statistics.influence_stats_worker import InfluenceStatsWorker
from src.statistics.lyrics_stats_worker import LyricsStatsWorker
from src.statistics.places_credits_stats_worker import PlacesCreditsStatsWorker
from src.statistics.rating_distribution_chart import RatingDistributionChart
from src.statistics.stats.audio import DSP_COLUMNS

# Highlight color for stat values in the HTML rich-text labels below. Qt's
# rich-text renderer doesn't resolve QSS for inline HTML color attributes,
# so this can't be sourced from themes/dark_mode.qss like widget styling can.
_HIGHLIGHT_COLOR = "#EA8599"


def _hl(text: object) -> str:
    """Wrap `text` in the stats-dialog highlight span (bold, accent colour)."""
    return f"<span style='color: {_HIGHLIGHT_COLOR}; font-weight: bold;'>{text}</span>"


def _lifespan_detail(d: dict) -> str:
    """`N years (begin-end)` tile text; the range uses an intentional en dash."""
    return f"{d['years']} years ({d['begin_year']}–{d['end_year']})"  # noqa: RUF001


def _match_detail(d: dict) -> str:
    """`P% matched (m/total)` secondary text for a chart-year tile."""
    return f"{d['completeness']}% matched ({d['matched']}/{d['total']})"


class StatisticsWorker(CancellableWorker):
    """Runs get_comprehensive_statistics() off the main thread.

    The stats query does ~20 sequential SQL queries with joins/group-bys;
    running it on the GUI thread froze the whole app (not just this dialog)
    every time the dialog opened.
    """

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, statistics, parent=None):
        super().__init__(parent)
        self.statistics = statistics

    def run(self):
        try:
            stats = self.statistics.get_comprehensive_statistics()
            self.finished.emit(stats)
        except SQLAlchemyError as e:
            logger.error(f"Error loading statistics: {e}")
            self.error.emit(str(e))
        finally:
            # Read-only. See CancellableWorker's _release_db_session docstring.
            self._release_db_session()


def _placeholder_label(text: str) -> QLabel:
    """Muted note used on tabs/sections whose content lands in a later
    phase of the statistics expansion, so the tab shell is stable now and
    later phases only add content rather than reshuffling layout."""
    label = QLabel(text)
    label.setObjectName("StatPlaceholderLabel")
    label.setWordWrap(True)
    return label


def _recompute_bar(on_click):
    """Right-aligned 'Recompute' button for a lazily-loaded tab. These tabs
    load once per dialog session (see the loading-model note on
    StatisticsWorker); this is the only way to refresh one without closing
    and reopening the whole dialog. Returns (layout, button) -- the caller
    adds the layout to its tab and keeps the button to disable it while a
    recompute is in flight."""
    bar = QHBoxLayout()
    bar.addStretch()
    button = QPushButton("Recompute")
    button.clicked.connect(on_click)
    bar.addWidget(button)
    return bar, button


class MusicStatsDialog(QDialog):
    def __init__(self, controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.stats = None
        self.worker = None
        self.influence_worker = None
        self.influence_stats = None
        self.audio_worker = None
        self.audio_stats = None
        self.genre_mood_worker = None
        self.genre_mood_stats = None
        self.album_worker = None
        self.album_stats = None
        self.artist_worker = None
        self.artist_stats = None
        self.places_credits_worker = None
        self.places_credits_stats = None
        self.lyrics_worker = None
        self.lyrics_stats = None
        self.setWindowTitle("Music Library Statistics")
        self.setMinimumSize(1100, 750)
        self.setup_ui()
        self.load_data()
        self._load_influence_tiles()
        self._load_audio_stats()
        self._load_genre_mood_stats()
        self._load_album_stats()
        self._load_artist_stats()
        self._load_places_credits_stats()
        self._load_lyrics_stats()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget()

        self.overview_tab = self.create_overview_tab()
        self.tab_widget.addTab(self.overview_tab, "Overview")

        self.library_health_tab = self.create_library_health_tab()
        self.tab_widget.addTab(self.library_health_tab, "Library Health")

        self.artists_tab = self.create_artists_tab()
        self.tab_widget.addTab(self.artists_tab, "Artists")

        self.albums_tab = self.create_albums_tab()
        self.tab_widget.addTab(self.albums_tab, "Albums")

        self.genres_moods_tab = self.create_genres_moods_tab()
        self.tab_widget.addTab(self.genres_moods_tab, "Genres && Moods")

        self.places_credits_tab = self.create_places_credits_tab()
        self.tab_widget.addTab(self.places_credits_tab, "Places && Credits")

        self.audio_profile_tab = self.create_audio_profile_tab()
        self.tab_widget.addTab(self.audio_profile_tab, "Audio Profile")

        self.lyrics_tab = self.create_lyrics_tab()
        self.tab_widget.addTab(self.lyrics_tab, "Lyrics")

        layout.addWidget(self.tab_widget)

    # ------------------------------------------------------------------ #
    #  Tab builders                                                        #
    # ------------------------------------------------------------------ #

    def create_overview_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        # Headline tiles — influence-graph-derived stats, lazy-loaded once
        # per dialog session (see _load_influence_tiles). Other tiles are
        # filled in as their phase lands.
        tiles_group = QGroupBox("Headlines")
        tiles_layout = QHBoxLayout(tiles_group)
        self.most_influential_tile = StatTileWidget("Most Influential Artist")
        self.most_eclectic_tile = StatTileWidget("Most Eclectic Artist")
        self.oldest_living_tile = StatTileWidget("Oldest Living Artist")
        self.common_birthdate_tile = StatTileWidget("Most Common Birthdate")
        for tile in [
            self.most_influential_tile,
            self.most_eclectic_tile,
            self.oldest_living_tile,
            self.common_birthdate_tile,
        ]:
            tiles_layout.addWidget(tile)
        layout.addWidget(tiles_group)

        headlines_recompute_bar, self.headlines_recompute_button = _recompute_bar(
            self._recompute_headline_tiles
        )
        layout.addLayout(headlines_recompute_bar)

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

    def create_artists_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        artist_recompute_bar, self.artist_recompute_button = _recompute_bar(
            self._recompute_artist_stats
        )
        layout.addLayout(artist_recompute_bar)

        artists_group = QGroupBox("Top Artists by Plays")
        artists_layout = QVBoxLayout(artists_group)
        self.top_artists_list = LeaderboardListWidget(value_suffix=" plays")
        artists_layout.addWidget(self.top_artists_list)
        layout.addWidget(artists_group)

        deathdate_group = QGroupBox("Dates")
        deathdate_layout = QHBoxLayout(deathdate_group)
        self.common_deathdate_tile = StatTileWidget("Most Common Deathdate")
        deathdate_layout.addWidget(self.common_deathdate_tile)
        layout.addWidget(deathdate_group)

        generation_group = QGroupBox("Average Rating by Generation")
        generation_layout = QVBoxLayout(generation_group)
        self.generation_ratings_list = LeaderboardListWidget()
        generation_layout.addWidget(self.generation_ratings_list)
        layout.addWidget(generation_group)

        type_group = QGroupBox("Artist Type")
        type_layout = QVBoxLayout(type_group)
        type_layout.addWidget(QLabel("Distribution:"))
        self.artist_type_chart = BarDistributionChart()
        type_layout.addWidget(self.artist_type_chart)
        type_rating_layout = QHBoxLayout()
        type_highest_box = QVBoxLayout()
        type_highest_box.addWidget(QLabel("Highest Rated Types:"))
        self.artist_type_highest_list = LeaderboardListWidget()
        type_highest_box.addWidget(self.artist_type_highest_list)
        type_lowest_box = QVBoxLayout()
        type_lowest_box.addWidget(QLabel("Lowest Rated Types:"))
        self.artist_type_lowest_list = LeaderboardListWidget()
        type_lowest_box.addWidget(self.artist_type_lowest_list)
        type_rating_layout.addLayout(type_highest_box)
        type_rating_layout.addLayout(type_lowest_box)
        type_layout.addLayout(type_rating_layout)
        type_layout.addWidget(QLabel("Highest Rated Artist of Each Type:"))
        self.artist_type_best_list = LeaderboardListWidget()
        type_layout.addWidget(self.artist_type_best_list)
        layout.addWidget(type_group)

        religion_group = QGroupBox("Artist Religion")
        religion_layout = QVBoxLayout(religion_group)
        religion_layout.addWidget(QLabel("Distribution:"))
        self.artist_religion_chart = BarDistributionChart()
        religion_layout.addWidget(self.artist_religion_chart)
        religion_layout.addWidget(QLabel("Rating Comparison:"))
        self.artist_religion_rating_list = LeaderboardListWidget()
        religion_layout.addWidget(self.artist_religion_rating_list)
        layout.addWidget(religion_group)

        gender_group = QGroupBox("Artist Gender")
        gender_layout = QVBoxLayout(gender_group)
        gender_layout.addWidget(QLabel("Rating Comparison:"))
        self.gender_rating_list = LeaderboardListWidget()
        gender_layout.addWidget(self.gender_rating_list)

        gender_layout.addWidget(QLabel("Highest / Lowest Rated Artists by Gender:"))
        self.gender_combo = QComboBox()
        self.gender_combo.currentTextChanged.connect(self._update_gender_leaderboard)
        gender_layout.addWidget(self.gender_combo)
        self.gender_tier = ThresholdTierWidget()
        self.gender_tier.tier_changed.connect(self._update_gender_leaderboard)
        gender_layout.addWidget(self.gender_tier)
        gender_lists_layout = QHBoxLayout()
        gender_highest_box = QVBoxLayout()
        gender_highest_box.addWidget(QLabel("Highest Rated:"))
        self.gender_highest_list = LeaderboardListWidget()
        gender_highest_box.addWidget(self.gender_highest_list)
        gender_lowest_box = QVBoxLayout()
        gender_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.gender_lowest_list = LeaderboardListWidget()
        gender_lowest_box.addWidget(self.gender_lowest_list)
        gender_lists_layout.addLayout(gender_highest_box)
        gender_lists_layout.addLayout(gender_lowest_box)
        gender_layout.addLayout(gender_lists_layout)
        layout.addWidget(gender_group)

        lifespan_group = QGroupBox("Lifespan")
        lifespan_layout = QHBoxLayout(lifespan_group)
        self.longest_lived_tile = StatTileWidget("Longest Lived")
        self.shortest_lived_tile = StatTileWidget("Shortest Lived")
        self.youngest_artist_tile = StatTileWidget("Youngest")
        for tile in [
            self.longest_lived_tile,
            self.shortest_lived_tile,
            self.youngest_artist_tile,
        ]:
            lifespan_layout.addWidget(tile)
        layout.addWidget(lifespan_group)

        layout.addWidget(
            _placeholder_label(
                "Role-credit-count stats, rating-by-role, per-role leaderboards, "
                "and highest-rated artist by country are on the Places && Credits "
                "tab (they're credit/place joins, not plain artist-table queries)."
            )
        )

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_albums_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        album_recompute_bar, self.album_recompute_button = _recompute_bar(
            self._recompute_album_stats
        )
        layout.addLayout(album_recompute_bar)

        albums_group = QGroupBox("Highest Rated Albums")
        albums_layout = QVBoxLayout(albums_group)
        self.top_albums_list = LeaderboardListWidget()
        albums_layout.addWidget(self.top_albums_list)
        layout.addWidget(albums_group)

        controlled_group = QGroupBox("Highest Rated Albums, Controlled by Track Count")
        controlled_layout = QVBoxLayout(controlled_group)
        self.album_rating_tier = ThresholdTierWidget(thresholds=(3, 5, 10))
        self.album_rating_tier.tier_changed.connect(
            self._update_album_rating_leaderboard
        )
        controlled_layout.addWidget(self.album_rating_tier)
        self.album_rating_list = LeaderboardListWidget()
        controlled_layout.addWidget(self.album_rating_list)
        layout.addWidget(controlled_group)

        release_group = QGroupBox("Release Dates && Countries")
        release_layout = QVBoxLayout(release_group)
        self.common_release_date_tile = StatTileWidget("Most Common Release Date")
        release_layout.addWidget(self.common_release_date_tile)
        release_layout.addWidget(QLabel("Release Year Distribution:"))
        self.release_year_chart = YearTimeSeriesChart()
        release_layout.addWidget(self.release_year_chart)
        release_layout.addWidget(QLabel("Release Country Distribution:"))
        self.release_country_chart = BarDistributionChart()
        release_layout.addWidget(self.release_country_chart)
        release_layout.addWidget(QLabel("Highest Rated Album by Country:"))
        self.highest_rated_by_country_list = LeaderboardListWidget()
        release_layout.addWidget(self.highest_rated_by_country_list)
        layout.addWidget(release_group)

        sales_group = QGroupBox("Sales")
        sales_layout = QVBoxLayout(sales_group)
        self.sales_chart = HistogramChart()
        sales_layout.addWidget(self.sales_chart)
        sales_lists_layout = QHBoxLayout()
        top_selling_box = QVBoxLayout()
        top_selling_box.addWidget(QLabel("Top 5 Selling:"))
        self.top_selling_list = LeaderboardListWidget()
        top_selling_box.addWidget(self.top_selling_list)
        bottom_selling_box = QVBoxLayout()
        bottom_selling_box.addWidget(QLabel("Bottom 5 Selling:"))
        self.bottom_selling_list = LeaderboardListWidget()
        bottom_selling_box.addWidget(self.bottom_selling_list)
        sales_lists_layout.addLayout(top_selling_box)
        sales_lists_layout.addLayout(bottom_selling_box)
        sales_layout.addLayout(sales_lists_layout)
        layout.addWidget(sales_group)

        diversity_group = QGroupBox("Most Diverse Albums by Genre Spread")
        diversity_layout = QVBoxLayout(diversity_group)
        self.genre_diversity_list = LeaderboardListWidget()
        diversity_layout.addWidget(self.genre_diversity_list)
        layout.addWidget(diversity_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_genres_moods_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        genre_mood_recompute_bar, self.genre_mood_recompute_button = _recompute_bar(
            self._recompute_genre_mood_stats
        )
        layout.addLayout(genre_mood_recompute_bar)

        genres_group = QGroupBox("Top Genres by Plays")
        genres_layout = QVBoxLayout(genres_group)
        self.top_genres_list = LeaderboardListWidget(value_suffix=" plays")
        genres_layout.addWidget(self.top_genres_list)
        layout.addWidget(genres_group)

        moods_group = QGroupBox("Top Moods by Plays")
        moods_layout = QVBoxLayout(moods_group)
        self.top_moods_list = LeaderboardListWidget(value_suffix=" plays")
        moods_layout.addWidget(self.top_moods_list)
        layout.addWidget(moods_group)

        rated_genres_group = QGroupBox(
            "Highest / Lowest Rated Genres (quick view, min 5 rated tracks)"
        )
        rated_genres_layout = QHBoxLayout(rated_genres_group)
        self.highest_rated_genres_list = LeaderboardListWidget()
        self.lowest_rated_genres_list = LeaderboardListWidget()
        rated_genres_layout.addWidget(self.highest_rated_genres_list)
        rated_genres_layout.addWidget(self.lowest_rated_genres_list)
        layout.addWidget(rated_genres_group)

        tiered_genres_group = QGroupBox("Highest / Lowest Rated Genres (power-of-10)")
        tiered_genres_layout = QVBoxLayout(tiered_genres_group)
        self.genre_rating_tier = ThresholdTierWidget()
        self.genre_rating_tier.tier_changed.connect(
            self._update_rated_genres_leaderboard
        )
        tiered_genres_layout.addWidget(self.genre_rating_tier)
        tiered_genres_lists_layout = QHBoxLayout()
        tiered_highest_box = QVBoxLayout()
        tiered_highest_box.addWidget(QLabel("Highest Rated:"))
        self.tiered_highest_genres_list = LeaderboardListWidget()
        tiered_highest_box.addWidget(self.tiered_highest_genres_list)
        tiered_lowest_box = QVBoxLayout()
        tiered_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.tiered_lowest_genres_list = LeaderboardListWidget()
        tiered_lowest_box.addWidget(self.tiered_lowest_genres_list)
        tiered_genres_lists_layout.addLayout(tiered_highest_box)
        tiered_genres_lists_layout.addLayout(tiered_lowest_box)
        tiered_genres_layout.addLayout(tiered_genres_lists_layout)
        layout.addWidget(tiered_genres_group)

        niche_group = QGroupBox("Most Niche Genre")
        niche_layout = QVBoxLayout(niche_group)
        self.most_niche_genre_tile = StatTileWidget("Deepest Nested Genre")
        niche_layout.addWidget(self.most_niche_genre_tile)
        layout.addWidget(niche_group)

        genre_count_group = QGroupBox("Genres by Track Count")
        genre_count_layout = QHBoxLayout(genre_count_group)
        top_count_box = QVBoxLayout()
        top_count_box.addWidget(QLabel("Top 5:"))
        self.top_genre_count_list = LeaderboardListWidget(value_suffix=" tracks")
        top_count_box.addWidget(self.top_genre_count_list)
        bottom_count_box = QVBoxLayout()
        bottom_count_box.addWidget(QLabel("Bottom 5:"))
        self.bottom_genre_count_list = LeaderboardListWidget(value_suffix=" tracks")
        bottom_count_box.addWidget(self.bottom_genre_count_list)
        genre_count_layout.addLayout(top_count_box)
        genre_count_layout.addLayout(bottom_count_box)
        layout.addWidget(genre_count_group)

        mood_rating_group = QGroupBox("Highest / Lowest Rated Mood (outlier-controlled)")
        mood_rating_layout = QHBoxLayout(mood_rating_group)
        self.highest_rated_moods_list = LeaderboardListWidget()
        self.lowest_rated_moods_list = LeaderboardListWidget()
        mood_rating_layout.addWidget(self.highest_rated_moods_list)
        mood_rating_layout.addWidget(self.lowest_rated_moods_list)
        layout.addWidget(mood_rating_group)

        mood_plays_group = QGroupBox("Most / Least Played Mood")
        mood_plays_layout = QHBoxLayout(mood_plays_group)
        self.most_played_moods_list = LeaderboardListWidget(value_suffix=" plays")
        self.least_played_moods_list = LeaderboardListWidget(value_suffix=" plays")
        mood_plays_layout.addWidget(self.most_played_moods_list)
        mood_plays_layout.addWidget(self.least_played_moods_list)
        layout.addWidget(mood_plays_group)

        representative_group = QGroupBox("Most Representative Tracks per Mood")
        representative_layout = QVBoxLayout(representative_group)
        representative_layout.addWidget(
            QLabel(
                "The 5 tracks whose lyrics match each auto-tagged mood's "
                "keyword list most strongly (by match density)."
            )
        )
        self.representative_mood_combo = QComboBox()
        self.representative_mood_combo.currentTextChanged.connect(
            self._update_representative_tracks_leaderboard
        )
        representative_layout.addWidget(self.representative_mood_combo)
        self.representative_tracks_list = LeaderboardListWidget(value_suffix="%")
        representative_layout.addWidget(self.representative_tracks_list)
        layout.addWidget(representative_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_places_credits_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        places_credits_recompute_bar, self.places_credits_recompute_button = (
            _recompute_bar(self._recompute_places_credits_stats)
        )
        layout.addLayout(places_credits_recompute_bar)

        places_group = QGroupBox("Highest / Lowest Rated Places (power-of-10)")
        places_layout = QVBoxLayout(places_group)
        self.place_rating_tier = ThresholdTierWidget()
        self.place_rating_tier.tier_changed.connect(self._update_place_rating_leaderboard)
        places_layout.addWidget(self.place_rating_tier)
        places_lists_layout = QHBoxLayout()
        place_highest_box = QVBoxLayout()
        place_highest_box.addWidget(QLabel("Highest Rated:"))
        self.place_highest_list = LeaderboardListWidget()
        place_highest_box.addWidget(self.place_highest_list)
        place_lowest_box = QVBoxLayout()
        place_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.place_lowest_list = LeaderboardListWidget()
        place_lowest_box.addWidget(self.place_lowest_list)
        places_lists_layout.addLayout(place_highest_box)
        places_lists_layout.addLayout(place_lowest_box)
        places_layout.addLayout(places_lists_layout)
        layout.addWidget(places_group)

        countries_group = QGroupBox(
            "Highest / Lowest Rated Countries (recursive rollup)"
        )
        countries_layout = QHBoxLayout(countries_group)
        self.country_highest_list = LeaderboardListWidget()
        self.country_lowest_list = LeaderboardListWidget()
        countries_layout.addWidget(self.country_highest_list)
        countries_layout.addWidget(self.country_lowest_list)
        layout.addWidget(countries_group)

        artist_by_country_group = QGroupBox("Highest Rated Artist by Country")
        artist_by_country_layout = QVBoxLayout(artist_by_country_group)
        self.artist_by_country_list = LeaderboardListWidget()
        artist_by_country_layout.addWidget(self.artist_by_country_list)
        layout.addWidget(artist_by_country_group)

        publishers_group = QGroupBox("Highest / Lowest Rated Publishers (by album count)")
        publishers_layout = QVBoxLayout(publishers_group)
        self.publisher_rating_tier = ThresholdTierWidget(thresholds=(5, 20, 100))
        self.publisher_rating_tier.tier_changed.connect(
            self._update_publisher_rating_leaderboard
        )
        publishers_layout.addWidget(self.publisher_rating_tier)
        publisher_lists_layout = QHBoxLayout()
        publisher_highest_box = QVBoxLayout()
        publisher_highest_box.addWidget(QLabel("Highest Rated:"))
        self.publisher_highest_list = LeaderboardListWidget()
        publisher_highest_box.addWidget(self.publisher_highest_list)
        publisher_lowest_box = QVBoxLayout()
        publisher_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.publisher_lowest_list = LeaderboardListWidget()
        publisher_lowest_box.addWidget(self.publisher_lowest_list)
        publisher_lists_layout.addLayout(publisher_highest_box)
        publisher_lists_layout.addLayout(publisher_lowest_box)
        publishers_layout.addLayout(publisher_lists_layout)
        layout.addWidget(publishers_group)

        composers_group = QGroupBox("Composers")
        composers_layout = QVBoxLayout(composers_group)
        composers_layout.addWidget(QLabel("Most Prolific:"))
        self.prolific_composer_list = LeaderboardListWidget(value_suffix=" tracks")
        composers_layout.addWidget(self.prolific_composer_list)
        self.composer_rating_tier = ThresholdTierWidget()
        self.composer_rating_tier.tier_changed.connect(
            self._update_composer_rating_leaderboard
        )
        composers_layout.addWidget(self.composer_rating_tier)
        composer_lists_layout = QHBoxLayout()
        composer_highest_box = QVBoxLayout()
        composer_highest_box.addWidget(QLabel("Highest Rated:"))
        self.composer_highest_list = LeaderboardListWidget()
        composer_highest_box.addWidget(self.composer_highest_list)
        composer_lowest_box = QVBoxLayout()
        composer_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.composer_lowest_list = LeaderboardListWidget()
        composer_lowest_box.addWidget(self.composer_lowest_list)
        composer_lists_layout.addLayout(composer_highest_box)
        composer_lists_layout.addLayout(composer_lowest_box)
        composers_layout.addLayout(composer_lists_layout)
        layout.addWidget(composers_group)

        role_counts_group = QGroupBox("Role Credit Counts")
        role_counts_layout = QHBoxLayout(role_counts_group)
        most_credits_box = QVBoxLayout()
        most_credits_box.addWidget(QLabel("Most Credited Roles (total):"))
        self.most_credits_list = LeaderboardListWidget(value_suffix=" credits")
        most_credits_box.addWidget(self.most_credits_list)
        most_distinct_box = QVBoxLayout()
        most_distinct_box.addWidget(QLabel("Most Distinct Roles:"))
        self.most_distinct_roles_list = LeaderboardListWidget(value_suffix=" roles")
        most_distinct_box.addWidget(self.most_distinct_roles_list)
        role_counts_layout.addLayout(most_credits_box)
        role_counts_layout.addLayout(most_distinct_box)
        layout.addWidget(role_counts_group)

        role_rating_group = QGroupBox(
            "Rating Comparison by Role (top 25% of roles by credit count)"
        )
        role_rating_layout = QVBoxLayout(role_rating_group)
        self.role_rating_list = LeaderboardListWidget()
        role_rating_layout.addWidget(self.role_rating_list)
        layout.addWidget(role_rating_group)

        role_explorer_group = QGroupBox("Prolific / Top Rated Artist by Role")
        role_explorer_layout = QVBoxLayout(role_explorer_group)
        self.role_combo = QComboBox()
        self.role_combo.currentTextChanged.connect(self._update_role_explorer)
        role_explorer_layout.addWidget(self.role_combo)
        role_explorer_layout.addWidget(QLabel("Most Prolific:"))
        self.role_prolific_list = LeaderboardListWidget(value_suffix=" tracks")
        role_explorer_layout.addWidget(self.role_prolific_list)
        self.role_rating_tier = ThresholdTierWidget()
        self.role_rating_tier.tier_changed.connect(self._update_role_explorer)
        role_explorer_layout.addWidget(self.role_rating_tier)
        role_rated_lists_layout = QHBoxLayout()
        role_rated_highest_box = QVBoxLayout()
        role_rated_highest_box.addWidget(QLabel("Highest Rated:"))
        self.role_rated_highest_list = LeaderboardListWidget()
        role_rated_highest_box.addWidget(self.role_rated_highest_list)
        role_rated_lowest_box = QVBoxLayout()
        role_rated_lowest_box.addWidget(QLabel("Lowest Rated:"))
        self.role_rated_lowest_list = LeaderboardListWidget()
        role_rated_lowest_box.addWidget(self.role_rated_lowest_list)
        role_rated_lists_layout.addLayout(role_rated_highest_box)
        role_rated_lists_layout.addLayout(role_rated_lowest_box)
        role_explorer_layout.addLayout(role_rated_lists_layout)
        layout.addWidget(role_explorer_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_audio_profile_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        audio_recompute_bar, self.audio_recompute_button = _recompute_bar(
            self._recompute_audio_stats
        )
        layout.addLayout(audio_recompute_bar)

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

        # BPM — with confidence toggle
        bpm_group = QGroupBox("BPM Distribution")
        bpm_layout = QVBoxLayout(bpm_group)
        self.bpm_confidence_checkbox = QCheckBox("Exclude confidence below 50%")
        self.bpm_confidence_checkbox.toggled.connect(self.load_audio_profile_data)
        bpm_layout.addWidget(self.bpm_confidence_checkbox)
        self.bpm_chart = HistogramChart(value_format="{:.0f}")
        bpm_layout.addWidget(self.bpm_chart)
        layout.addWidget(bpm_group)

        # Key — with confidence toggle
        key_group = QGroupBox("Key Distribution")
        key_layout = QVBoxLayout(key_group)
        self.key_confidence_checkbox = QCheckBox("Exclude confidence below 50%")
        self.key_confidence_checkbox.toggled.connect(self.load_audio_profile_data)
        key_layout.addWidget(self.key_confidence_checkbox)
        self.key_chart = BarDistributionChart()
        key_layout.addWidget(self.key_chart)
        layout.addWidget(key_group)

        # Track gain + quietest/loudest
        gain_group = QGroupBox("Track Gain")
        gain_layout = QVBoxLayout(gain_group)
        self.track_gain_chart = HistogramChart(unit=" dB", value_format="{:.1f}")
        gain_layout.addWidget(self.track_gain_chart)
        quiet_loud_layout = QHBoxLayout()
        quietest_box = QVBoxLayout()
        quietest_box.addWidget(QLabel("Quietest 10:"))
        self.quietest_list = LeaderboardListWidget(value_suffix=" dB")
        quietest_box.addWidget(self.quietest_list)
        loudest_box = QVBoxLayout()
        loudest_box.addWidget(QLabel("Loudest 10:"))
        self.loudest_list = LeaderboardListWidget(value_suffix=" dB")
        loudest_box.addWidget(self.loudest_list)
        quiet_loud_layout.addLayout(quietest_box)
        quiet_loud_layout.addLayout(loudest_box)
        gain_layout.addLayout(quiet_loud_layout)
        layout.addWidget(gain_group)

        # Time signature / file size
        misc_group = QGroupBox("Time Signature && File Size")
        misc_layout = QHBoxLayout(misc_group)
        time_sig_box = QVBoxLayout()
        time_sig_box.addWidget(QLabel("Time Signature:"))
        self.time_signature_confidence_checkbox = QCheckBox(
            "Exclude confidence below 50%"
        )
        self.time_signature_confidence_checkbox.toggled.connect(
            self.load_audio_profile_data
        )
        time_sig_box.addWidget(self.time_signature_confidence_checkbox)
        self.time_signature_chart = BarDistributionChart()
        time_sig_box.addWidget(self.time_signature_chart)
        file_size_box = QVBoxLayout()
        file_size_box.addWidget(QLabel("File Size (MB):"))
        self.file_size_chart = HistogramChart(unit=" MB", value_format="{:.1f}")
        file_size_box.addWidget(self.file_size_chart)
        misc_layout.addLayout(time_sig_box)
        misc_layout.addLayout(file_size_box)
        layout.addWidget(misc_group)

        # Instrumental / classical
        split_group = QGroupBox("Instrumental && Classical")
        split_layout = QHBoxLayout(split_group)
        instrumental_box = QVBoxLayout()
        instrumental_box.addWidget(QLabel("Instrumental:"))
        self.instrumental_chart = BarDistributionChart()
        instrumental_box.addWidget(self.instrumental_chart)
        classical_box = QVBoxLayout()
        classical_box.addWidget(QLabel("Classical:"))
        self.classical_chart = BarDistributionChart()
        classical_box.addWidget(self.classical_chart)
        split_layout.addLayout(instrumental_box)
        split_layout.addLayout(classical_box)
        layout.addWidget(split_group)

        # Advanced DSP properties — one metric at a time via selector, since
        # all 16 columns' distributions/top10/bottom10 are already computed
        # up front (see stats/audio.py), switching the selector is instant.
        dsp_group = QGroupBox("Advanced Audio Properties")
        dsp_layout = QVBoxLayout(dsp_group)
        self.dsp_metric_combo = QComboBox()
        for label, _attr in DSP_COLUMNS:
            self.dsp_metric_combo.addItem(label)
        self.dsp_metric_combo.currentTextChanged.connect(self.load_dsp_metric)
        dsp_layout.addWidget(self.dsp_metric_combo)
        self.dsp_chart = HistogramChart(value_format="{:.2f}")
        dsp_layout.addWidget(self.dsp_chart)
        dsp_lists_layout = QHBoxLayout()
        dsp_top_box = QVBoxLayout()
        dsp_top_box.addWidget(QLabel("Top 10:"))
        self.dsp_top_list = LeaderboardListWidget()
        dsp_top_box.addWidget(self.dsp_top_list)
        dsp_bottom_box = QVBoxLayout()
        dsp_bottom_box.addWidget(QLabel("Bottom 10:"))
        self.dsp_bottom_list = LeaderboardListWidget()
        dsp_bottom_box.addWidget(self.dsp_bottom_list)
        dsp_lists_layout.addLayout(dsp_top_box)
        dsp_lists_layout.addLayout(dsp_bottom_box)
        dsp_layout.addLayout(dsp_lists_layout)
        layout.addWidget(dsp_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def create_lyrics_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        lyrics_recompute_bar, self.lyrics_recompute_button = _recompute_bar(
            self._recompute_lyrics_stats
        )
        layout.addLayout(lyrics_recompute_bar)

        cloud_group = QGroupBox(
            "Word Cloud (words appearing in 5+ distinct tracks)"
        )
        cloud_layout = QVBoxLayout(cloud_group)
        self.lyrics_word_cloud = WordCloudWidget()
        cloud_layout.addWidget(self.lyrics_word_cloud)
        layout.addWidget(cloud_group)

        weighted_group = QGroupBox(
            "Words Skewed Toward Higher / Lower Rated Tracks"
        )
        weighted_layout = QHBoxLayout(weighted_group)
        high_box = QVBoxLayout()
        high_box.addWidget(QLabel("Higher-Rated Tracks:"))
        self.lyrics_high_words = WordCloudWidget()
        high_box.addWidget(self.lyrics_high_words)
        low_box = QVBoxLayout()
        low_box.addWidget(QLabel("Lower-Rated Tracks:"))
        self.lyrics_low_words = WordCloudWidget()
        low_box.addWidget(self.lyrics_low_words)
        weighted_layout.addLayout(high_box)
        weighted_layout.addLayout(low_box)
        layout.addWidget(weighted_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    def load_data(self):
        """Kick off a background fetch of all statistics data. Runs once
        when the dialog opens -- the data is a snapshot as of open time, not
        a live view, so there's no auto-refresh."""
        if self.worker is not None and self.worker.isRunning():
            return

        self.worker = StatisticsWorker(self.controller.statistics)
        self.worker.finished.connect(self.on_stats_loaded)
        self.worker.error.connect(self.on_stats_error)
        self.worker.start()

    def _load_influence_tiles(self):
        """Lazy-load the influence-graph-derived Overview tiles. Kept as a
        separate worker from load_data() since it's an O(seconds) graph
        computation over the whole artist influence graph, not a cheap
        aggregate -- no need to hold up the rest of the dialog on it. Runs
        once per dialog session; re-run manually via the Overview tab's
        Recompute button (see _recompute_headline_tiles)."""
        if self.influence_worker is not None and self.influence_worker.isRunning():
            return

        self.influence_worker = InfluenceStatsWorker(self.controller.get)
        self.influence_worker.finished.connect(self.on_influence_stats_loaded)
        self.influence_worker.error.connect(self.on_influence_stats_error)
        self.influence_worker.start()

    def on_influence_stats_loaded(self, stats):
        self.influence_stats = stats
        most_influential = stats.get("most_influential")
        if most_influential:
            name, score = most_influential
            self.most_influential_tile.set_data(name, f"{score} artists influenced")
        else:
            self.most_influential_tile.set_data("N/A")

        most_eclectic = stats.get("most_eclectic")
        if most_eclectic:
            name, bridges = stats.get("most_eclectic")
            self.most_eclectic_tile.set_data(
                name, f"spans {bridges} communities"
            )
        else:
            self.most_eclectic_tile.set_data("N/A")
        self.headlines_recompute_button.setEnabled(True)

    def on_influence_stats_error(self, message):
        self.most_influential_tile.set_data("N/A")
        self.most_eclectic_tile.set_data("N/A")
        self.headlines_recompute_button.setEnabled(True)

    def _recompute_headline_tiles(self):
        """Refresh the Overview tab's lazily-loaded tiles -- most
        influential/eclectic artist (influence_worker) and oldest living
        artist (artist_worker, shared with the Artists tab's own Recompute
        button)."""
        self.headlines_recompute_button.setEnabled(False)
        self._load_influence_tiles()
        self._load_artist_stats()

    def _load_audio_stats(self):
        """Lazy-load the Audio Profile tab's distributions/leaderboards, as
        a separate worker from load_data() -- see the module docstring in
        stats/audio.py for why it's heavier than the rest. Runs once per
        dialog session."""
        if self.audio_worker is not None and self.audio_worker.isRunning():
            return

        self.audio_worker = AudioStatsWorker(self.controller.statistics.audio)
        self.audio_worker.finished.connect(self.on_audio_stats_loaded)
        self.audio_worker.error.connect(self.on_audio_stats_error)
        self.audio_worker.start()

    def on_audio_stats_loaded(self, stats):
        self.audio_stats = stats
        self.load_audio_profile_data()
        self.audio_recompute_button.setEnabled(True)

    def on_audio_stats_error(self, message):
        self.audio_recompute_button.setEnabled(True)

    def _recompute_audio_stats(self):
        self.audio_recompute_button.setEnabled(False)
        self._load_audio_stats()

    def _load_genre_mood_stats(self):
        """Lazy-load the Genres & Moods tab's Phase-3 content (power-of-10
        leaderboards, most niche genre, outlier-controlled mood ratings).
        Runs once per dialog session."""
        if self.genre_mood_worker is not None and self.genre_mood_worker.isRunning():
            return

        self.genre_mood_worker = GenreMoodStatsWorker(self.controller.statistics.genres_moods)
        self.genre_mood_worker.finished.connect(self.on_genre_mood_stats_loaded)
        self.genre_mood_worker.error.connect(self.on_genre_mood_stats_error)
        self.genre_mood_worker.start()

    def on_genre_mood_stats_loaded(self, stats):
        self.genre_mood_stats = stats
        self.load_genre_mood_phase3_data()
        self.genre_mood_recompute_button.setEnabled(True)

    def on_genre_mood_stats_error(self, message):
        self.genre_mood_recompute_button.setEnabled(True)

    def _recompute_genre_mood_stats(self):
        self.genre_mood_recompute_button.setEnabled(False)
        self._load_genre_mood_stats()

    def _load_album_stats(self):
        """Lazy-load the Albums tab's Phase-3 content. Runs once per dialog
        session."""
        if self.album_worker is not None and self.album_worker.isRunning():
            return

        self.album_worker = AlbumStatsWorker(self.controller.statistics.albums)
        self.album_worker.finished.connect(self.on_album_stats_loaded)
        self.album_worker.error.connect(self.on_album_stats_error)
        self.album_worker.start()

    def on_album_stats_loaded(self, stats):
        self.album_stats = stats
        self.load_album_phase3_data()
        self.album_recompute_button.setEnabled(True)

    def on_album_stats_error(self, message):
        self.album_recompute_button.setEnabled(True)

    def _recompute_album_stats(self):
        self.album_recompute_button.setEnabled(False)
        self._load_album_stats()

    def _load_artist_stats(self):
        """Lazy-load the Artists tab's Phase-3 content. Runs once per dialog
        session."""
        if self.artist_worker is not None and self.artist_worker.isRunning():
            return

        self.artist_worker = ArtistStatsWorker(self.controller.statistics.artists)
        self.artist_worker.finished.connect(self.on_artist_stats_loaded)
        self.artist_worker.error.connect(self.on_artist_stats_error)
        self.artist_worker.start()

    def on_artist_stats_loaded(self, stats):
        self.artist_stats = stats
        self.load_artist_stats_data()
        self.artist_recompute_button.setEnabled(True)
        self.headlines_recompute_button.setEnabled(True)

    def on_artist_stats_error(self, message):
        self.artist_recompute_button.setEnabled(True)
        self.headlines_recompute_button.setEnabled(True)

    def _recompute_artist_stats(self):
        self.artist_recompute_button.setEnabled(False)
        self._load_artist_stats()

    def _load_places_credits_stats(self):
        """Lazy-load the Places & Credits tab's content. Runs once per
        dialog session -- the heaviest of the per-tab workers (recursive
        country/artist-by-country rollups plus a per-role leaderboard loop)."""
        if (
            self.places_credits_worker is not None
            and self.places_credits_worker.isRunning()
        ):
            return

        self.places_credits_worker = PlacesCreditsStatsWorker(
            self.controller.statistics.places_credits
        )
        self.places_credits_worker.finished.connect(self.on_places_credits_stats_loaded)
        self.places_credits_worker.error.connect(self.on_places_credits_stats_error)
        self.places_credits_worker.start()

    def on_places_credits_stats_loaded(self, stats):
        self.places_credits_stats = stats
        self.load_places_credits_data()
        self.places_credits_recompute_button.setEnabled(True)

    def on_places_credits_stats_error(self, message):
        self.places_credits_recompute_button.setEnabled(True)

    def _recompute_places_credits_stats(self):
        self.places_credits_recompute_button.setEnabled(False)
        self._load_places_credits_stats()

    def _load_lyrics_stats(self):
        """Lazy-load the Lyrics tab's content. Runs once per dialog
        session -- tokenizing every lyricized track's text is the heaviest
        per-tab computation after the recursive places/credits rollups."""
        if self.lyrics_worker is not None and self.lyrics_worker.isRunning():
            return

        self.lyrics_worker = LyricsStatsWorker(self.controller.statistics.lyrics)
        self.lyrics_worker.finished.connect(self.on_lyrics_stats_loaded)
        self.lyrics_worker.error.connect(self.on_lyrics_stats_error)
        self.lyrics_worker.start()

    def on_lyrics_stats_loaded(self, stats):
        self.lyrics_stats = stats
        self.load_lyrics_data()
        self.lyrics_recompute_button.setEnabled(True)

    def on_lyrics_stats_error(self, message):
        self.lyrics_recompute_button.setEnabled(True)

    def _recompute_lyrics_stats(self):
        self.lyrics_recompute_button.setEnabled(False)
        self._load_lyrics_stats()

    def load_lyrics_data(self):
        if not self.lyrics_stats:
            return

        self.lyrics_word_cloud.set_data(self.lyrics_stats.get("word_cloud", []))
        weighted_words = self.lyrics_stats.get("weighted_words", {})
        self.lyrics_high_words.set_data(weighted_words.get("high", []))
        self.lyrics_low_words.set_data(weighted_words.get("low", []))

    def on_stats_loaded(self, stats):
        """Populate the UI once the background worker has fetched the stats."""
        self.stats = stats
        try:
            self.load_overview_data()
            self.load_library_health_data()
            self.load_artists_data()
            self.load_albums_data()
            self.load_genres_moods_data()
        except (KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error updating statistics UI: {e}")

    def on_stats_error(self, message):
        pass

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
        self.total_play_time_label.setText(f"Total Play Time: {_hl(play_time)}")

        file_size = self.format_file_size(stats["total_file_size"])
        self.total_file_size_label.setText(f"Total File Size: {_hl(file_size)}")

        # Overall completeness summary label
        overall = stats.get("overall_metadata_completeness", 0)
        self.metadata_completeness_label.setText(
            f"Metadata Complete: {self.format_stat_value(overall, False)}%"
        )

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
            self.most_played_artist_label.setText(f"Most Played Artist: {_hl(name)}")
        else:
            self.most_played_artist_label.setText("Most Played Artist: N/A")

        top_genres = leaderboards.get("top_genres", [])
        if top_genres:
            name, _ = top_genres[0]
            self.most_played_genre_label.setText(f"Most Played Genre: {_hl(name)}")
        else:
            self.most_played_genre_label.setText("Most Played Genre: N/A")

        highest_rated_artists = leaderboards.get("highest_rated_artists", [])
        if highest_rated_artists:
            name, avg_rating = highest_rated_artists[0]
            self.highest_rated_artist_label.setText(
                f"Highest Rated Artist: {_hl(f'{name} ({avg_rating:.1f})')}"
            )
        else:
            self.highest_rated_artist_label.setText("Highest Rated Artist: N/A")

        highest_rated_albums = leaderboards.get("highest_rated_albums", [])
        if highest_rated_albums:
            name, avg_rating = highest_rated_albums[0]
            self.highest_rated_album_label.setText(
                f"Highest Rated Album: {_hl(f'{name} ({avg_rating:.1f})')}"
            )
        else:
            self.highest_rated_album_label.setText("Highest Rated Album: N/A")

        highest_rated_genres = leaderboards.get("highest_rated_genres", [])
        if highest_rated_genres:
            name, avg_rating = highest_rated_genres[0]
            self.highest_rated_genre_label.setText(
                f"Highest Rated Genre: {_hl(f'{name} ({avg_rating:.1f})')}"
            )
        else:
            self.highest_rated_genre_label.setText("Highest Rated Genre: N/A")

        lowest_rated_genres = leaderboards.get("lowest_rated_genres", [])
        if lowest_rated_genres:
            name, avg_rating = lowest_rated_genres[0]
            self.lowest_rated_genre_label.setText(
                f"Lowest Rated Genre: {_hl(f'{name} ({avg_rating:.1f})')}"
            )
        else:
            self.lowest_rated_genre_label.setText("Lowest Rated Genre: N/A")

        birthdate = stats.get("most_common_birthdate")
        if birthdate:
            self.common_birthdate_tile.set_data(
                birthdate["label"], f"{birthdate['count']} artists"
            )
        else:
            self.common_birthdate_tile.set_data("N/A")

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
        distribution = {
            float(k): v for k, v in ratings_data.get("distribution", {}).items()
        }

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
        except (KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error loading file format data: {e}")
            self.format_labels[0].setText("Error loading file format data")

    def load_artists_data(self):
        """Load artists tab data."""
        leaderboards = self.stats.get("leaderboards", {})
        top_artists = leaderboards.get("top_artists", [])
        self.top_artists_list.set_data(
            [(name, plays, None) for name, plays in top_artists]
        )

        deathdate = self.stats.get("most_common_deathdate")
        if deathdate:
            self.common_deathdate_tile.set_data(
                deathdate["label"], f"{deathdate['count']} artists"
            )
        else:
            self.common_deathdate_tile.set_data("N/A")

    def load_albums_data(self):
        """Load albums tab data."""
        leaderboards = self.stats.get("leaderboards", {})
        top_albums = leaderboards.get("highest_rated_albums", [])
        self.top_albums_list.set_data(
            [(name, rating, None) for name, rating in top_albums]
        )

        release_date = self.stats.get("most_common_album_release_date")
        if release_date:
            self.common_release_date_tile.set_data(
                release_date["label"], f"{release_date['count']} albums"
            )
        else:
            self.common_release_date_tile.set_data("N/A")

        year_distribution = self.stats.get("album_release_year_distribution", {})
        self.release_year_chart.set_data(
            {str(year): count for year, count in year_distribution.items()}
        )

    def load_genres_moods_data(self):
        """Load genres and moods tab data."""
        leaderboards = self.stats.get("leaderboards", {})

        top_genres = leaderboards.get("top_genres", [])
        self.top_genres_list.set_data(
            [(name, plays, None) for name, plays in top_genres]
        )

        top_moods = leaderboards.get("top_moods", [])
        self.top_moods_list.set_data(
            [(name, plays, None) for name, plays in top_moods]
        )

        highest_rated_genres = leaderboards.get("highest_rated_genres", [])
        self.highest_rated_genres_list.set_data(
            [(name, rating, None) for name, rating in highest_rated_genres]
        )

        lowest_rated_genres = leaderboards.get("lowest_rated_genres", [])
        self.lowest_rated_genres_list.set_data(
            [(name, rating, None) for name, rating in lowest_rated_genres]
        )

    def _rating_rows_with_n(self, rows):
        """Remap (name, value, n) rating rows into LeaderboardListWidget's
        (name, value, secondary_label) shape, with n as the secondary note."""
        return [(name, value, f"{n} tracks") for name, value, n in rows]

    def load_genre_mood_phase3_data(self):
        """Load the Genres & Moods tab's Phase-3 content (already fetched
        in self.genre_mood_stats by the lazy GenreMoodStatsWorker)."""
        stats = self.genre_mood_stats
        if stats is None:
            return

        leaderboard = stats.get("rated_genres_leaderboard", {})
        highest_dict = leaderboard.get("highest", {})
        lowest_dict = leaderboard.get("lowest", {})
        non_empty = [
            t
            for t in set(highest_dict) | set(lowest_dict)
            if highest_dict.get(t) or lowest_dict.get(t)
        ]
        self.genre_rating_tier.set_thresholds_available(
            non_empty or list(highest_dict.keys())
        )
        self._update_rated_genres_leaderboard()

        niche = stats.get("most_niche_genre")
        if niche:
            self.most_niche_genre_tile.set_data(niche["name"], niche["path"])
        else:
            self.most_niche_genre_tile.set_data("N/A")

        genre_counts = stats.get("genres_by_track_count", {})
        self.top_genre_count_list.set_data(
            [(name, count, None) for name, count in genre_counts.get("top", [])]
        )
        self.bottom_genre_count_list.set_data(
            [(name, count, None) for name, count in genre_counts.get("bottom", [])]
        )

        mood_ratings = stats.get("mood_ratings_outlier_controlled", {})
        self.highest_rated_moods_list.set_data(
            self._rating_rows_with_n(mood_ratings.get("highest", []))
        )
        self.lowest_rated_moods_list.set_data(
            self._rating_rows_with_n(mood_ratings.get("lowest", []))
        )

        mood_plays = stats.get("mood_play_counts", {})
        self.most_played_moods_list.set_data(
            [(name, plays, None) for name, plays in mood_plays.get("most_played", [])]
        )
        self.least_played_moods_list.set_data(
            [
                (name, plays, None)
                for name, plays in mood_plays.get("least_played", [])
            ]
        )

        # Most representative tracks per mood -- populate the selector from
        # whatever moods have scored tracks, then show the first one. Block
        # the combo's signal during repopulation so the explicit update
        # call below is the only one that fires (and fires exactly once).
        representative = stats.get("representative_tracks_per_mood", {})
        self.representative_mood_combo.blockSignals(True)
        self.representative_mood_combo.clear()
        self.representative_mood_combo.addItems(sorted(representative.keys()))
        self.representative_mood_combo.setEnabled(bool(representative))
        self.representative_mood_combo.blockSignals(False)
        self._update_representative_tracks_leaderboard()

    def _update_representative_tracks_leaderboard(self, *_args):
        if self.genre_mood_stats is None:
            return
        representative = self.genre_mood_stats.get("representative_tracks_per_mood", {})
        mood_name = self.representative_mood_combo.currentText()
        self.representative_tracks_list.set_data(
            [
                (track_name, round(score * 100, 2), artist)
                for track_name, artist, score in representative.get(mood_name, [])
            ]
        )

    def _update_rated_genres_leaderboard(self, *_args):
        if self.genre_mood_stats is None:
            return
        leaderboard = self.genre_mood_stats.get("rated_genres_leaderboard", {})
        threshold = self.genre_rating_tier.current_threshold()
        self.tiered_highest_genres_list.set_data(
            self._rating_rows_with_n(leaderboard.get("highest", {}).get(threshold, []))
        )
        self.tiered_lowest_genres_list.set_data(
            self._rating_rows_with_n(leaderboard.get("lowest", {}).get(threshold, []))
        )

    def load_album_phase3_data(self):
        """Load the Albums tab's Phase-3 content (already fetched in
        self.album_stats by the lazy AlbumStatsWorker)."""
        stats = self.album_stats
        if stats is None:
            return

        leaderboard = stats.get("rating_by_track_count", {})
        non_empty = [t for t, rows in leaderboard.items() if rows]
        self.album_rating_tier.set_thresholds_available(
            non_empty or list(leaderboard.keys())
        )
        self._update_album_rating_leaderboard()

        self.release_country_chart.set_data(stats.get("release_country_distribution"))

        highest_by_country = stats.get("highest_rated_album_by_country", {})
        country_rows = sorted(
            [
                (country, rating, album)
                for country, (album, rating) in highest_by_country.items()
            ],
            key=lambda r: r[1],
            reverse=True,
        )[:10]
        self.highest_rated_by_country_list.set_data(country_rows)

        self.sales_chart.set_data(stats.get("sales_distribution"))

        top_bottom_selling = stats.get("top_bottom_selling_albums", {})
        self.top_selling_list.set_data(
            [
                (name, sales, artist)
                for name, artist, sales in top_bottom_selling.get("top", [])
            ]
        )
        self.bottom_selling_list.set_data(
            [
                (name, sales, artist)
                for name, artist, sales in top_bottom_selling.get("bottom", [])
            ]
        )

        diverse_albums = stats.get("most_diverse_albums_by_genre", [])
        self.genre_diversity_list.set_data(
            [
                (name, score, f"{branches} genre branches")
                for name, score, branches in diverse_albums
            ]
        )

    def _update_album_rating_leaderboard(self, *_args):
        if self.album_stats is None:
            return
        leaderboard = self.album_stats.get("rating_by_track_count", {})
        threshold = self.album_rating_tier.current_threshold()
        self.album_rating_list.set_data(
            self._rating_rows_with_n(leaderboard.get(threshold, []))
        )

    def load_artist_stats_data(self):
        """Load the Artists tab's Phase-3 content (already fetched in
        self.artist_stats by the lazy ArtistStatsWorker)."""
        stats = self.artist_stats
        if stats is None:
            return

        self.generation_ratings_list.set_data(
            self._rating_rows_with_n(stats.get("generation_ratings", []))
        )

        self.artist_type_chart.set_data(stats.get("artist_type_distribution"))

        type_rating = stats.get("artist_type_rating", {})
        self.artist_type_highest_list.set_data(
            self._rating_rows_with_n(type_rating.get("highest", []))
        )
        self.artist_type_lowest_list.set_data(
            self._rating_rows_with_n(type_rating.get("lowest", []))
        )

        best_by_type = stats.get("highest_rated_artist_per_type", {})
        type_rows = sorted(
            [
                (type_name, rating, artist)
                for type_name, (artist, rating) in best_by_type.items()
            ],
            key=lambda r: r[1],
            reverse=True,
        )
        self.artist_type_best_list.set_data(type_rows)

        self.artist_religion_chart.set_data(
            stats.get("artist_religion_distribution")
        )
        self.artist_religion_rating_list.set_data(
            self._rating_rows_with_n(stats.get("religion_rating_comparison", []))
        )

        self.gender_rating_list.set_data(
            self._rating_rows_with_n(stats.get("gender_rating_comparison", []))
        )

        by_gender = stats.get("rated_artists_by_gender", {})
        genders = sorted(by_gender.keys())
        self.gender_combo.blockSignals(True)
        self.gender_combo.clear()
        self.gender_combo.addItems(genders)
        self.gender_combo.blockSignals(False)
        self._update_gender_leaderboard()

        lifespan = stats.get("lifespan_stats", {})

        oldest_living = lifespan.get("oldest_living")
        if oldest_living:
            self.oldest_living_tile.set_data(
                oldest_living["name"], f"born {oldest_living['begin_year']}"
            )
        else:
            self.oldest_living_tile.set_data("N/A")

        youngest = lifespan.get("youngest")
        if youngest:
            self.youngest_artist_tile.set_data(
                youngest["name"], f"born {youngest['begin_year']}"
            )
        else:
            self.youngest_artist_tile.set_data("N/A")

        longest_lived = lifespan.get("longest_lived")
        if longest_lived:
            self.longest_lived_tile.set_data(longest_lived["name"], _lifespan_detail(longest_lived))
        else:
            self.longest_lived_tile.set_data("N/A")

        shortest_lived = lifespan.get("shortest_lived")
        if shortest_lived:
            self.shortest_lived_tile.set_data(
                shortest_lived["name"], _lifespan_detail(shortest_lived)
            )
        else:
            self.shortest_lived_tile.set_data("N/A")

    def _update_gender_leaderboard(self, *_args):
        if self.artist_stats is None:
            return
        gender = self.gender_combo.currentText()
        by_gender = self.artist_stats.get("rated_artists_by_gender", {})
        entry = by_gender.get(gender, {})
        highest_dict = entry.get("highest", {})
        lowest_dict = entry.get("lowest", {})
        non_empty = [
            t
            for t in set(highest_dict) | set(lowest_dict)
            if highest_dict.get(t) or lowest_dict.get(t)
        ]
        self.gender_tier.set_thresholds_available(
            non_empty or list(highest_dict.keys())
        )
        threshold = self.gender_tier.current_threshold()
        self.gender_highest_list.set_data(
            self._rating_rows_with_n(highest_dict.get(threshold, []))
        )
        self.gender_lowest_list.set_data(
            self._rating_rows_with_n(lowest_dict.get(threshold, []))
        )

    def _rows_with_note(self, rows, note):
        """Like _rating_rows_with_n, but for leaderboards whose count isn't
        a track count (e.g. publishers counted by album)."""
        return [(name, value, f"{n} {note}") for name, value, n in rows]

    def load_places_credits_data(self):
        """Load the Places & Credits tab's content (already fetched in
        self.places_credits_stats by the lazy PlacesCreditsStatsWorker)."""
        stats = self.places_credits_stats
        if stats is None:
            return

        place_leaderboard = stats.get("rated_places_leaderboard", {})
        highest_dict = place_leaderboard.get("highest", {})
        lowest_dict = place_leaderboard.get("lowest", {})
        non_empty = [
            t
            for t in set(highest_dict) | set(lowest_dict)
            if highest_dict.get(t) or lowest_dict.get(t)
        ]
        self.place_rating_tier.set_thresholds_available(
            non_empty or list(highest_dict.keys())
        )
        self._update_place_rating_leaderboard()

        countries = stats.get("rated_countries", {})
        self.country_highest_list.set_data(
            self._rating_rows_with_n(countries.get("highest", []))
        )
        self.country_lowest_list.set_data(
            self._rating_rows_with_n(countries.get("lowest", []))
        )

        highest_by_country = stats.get("highest_rated_artist_by_country", {})
        country_rows = sorted(
            [
                (country, rating, artist)
                for country, (artist, rating) in highest_by_country.items()
            ],
            key=lambda r: r[1],
            reverse=True,
        )[:10]
        self.artist_by_country_list.set_data(country_rows)

        publisher_leaderboard = stats.get("rated_publishers_leaderboard", {})
        pub_highest_dict = publisher_leaderboard.get("highest", {})
        pub_lowest_dict = publisher_leaderboard.get("lowest", {})
        pub_non_empty = [
            t
            for t in set(pub_highest_dict) | set(pub_lowest_dict)
            if pub_highest_dict.get(t) or pub_lowest_dict.get(t)
        ]
        self.publisher_rating_tier.set_thresholds_available(
            pub_non_empty or list(pub_highest_dict.keys())
        )
        self._update_publisher_rating_leaderboard()

        self.prolific_composer_list.set_data(
            [(name, count, None) for name, count in stats.get("most_prolific_composer", [])]
        )

        composer_leaderboard = stats.get("rated_composers_leaderboard", {})
        comp_highest_dict = composer_leaderboard.get("highest", {})
        comp_lowest_dict = composer_leaderboard.get("lowest", {})
        comp_non_empty = [
            t
            for t in set(comp_highest_dict) | set(comp_lowest_dict)
            if comp_highest_dict.get(t) or comp_lowest_dict.get(t)
        ]
        self.composer_rating_tier.set_thresholds_available(
            comp_non_empty or list(comp_highest_dict.keys())
        )
        self._update_composer_rating_leaderboard()

        role_counts = stats.get("role_credit_counts", {})
        self.most_credits_list.set_data(
            [(name, count, None) for name, count in role_counts.get("most_credits", [])]
        )
        self.most_distinct_roles_list.set_data(
            [
                (name, count, None)
                for name, count in role_counts.get("most_distinct_roles", [])
            ]
        )

        self.role_rating_list.set_data(
            self._rating_rows_with_n(stats.get("role_rating_comparison", []))
        )

        roles = sorted(stats.get("prolific_artist_by_role", {}).keys())
        self.role_combo.blockSignals(True)
        self.role_combo.clear()
        self.role_combo.addItems(roles)
        self.role_combo.blockSignals(False)
        self._update_role_explorer()

    def _update_place_rating_leaderboard(self, *_args):
        if self.places_credits_stats is None:
            return
        leaderboard = self.places_credits_stats.get("rated_places_leaderboard", {})
        threshold = self.place_rating_tier.current_threshold()
        self.place_highest_list.set_data(
            self._rating_rows_with_n(leaderboard.get("highest", {}).get(threshold, []))
        )
        self.place_lowest_list.set_data(
            self._rating_rows_with_n(leaderboard.get("lowest", {}).get(threshold, []))
        )

    def _update_publisher_rating_leaderboard(self, *_args):
        if self.places_credits_stats is None:
            return
        leaderboard = self.places_credits_stats.get("rated_publishers_leaderboard", {})
        threshold = self.publisher_rating_tier.current_threshold()
        self.publisher_highest_list.set_data(
            self._rows_with_note(
                leaderboard.get("highest", {}).get(threshold, []), "albums"
            )
        )
        self.publisher_lowest_list.set_data(
            self._rows_with_note(
                leaderboard.get("lowest", {}).get(threshold, []), "albums"
            )
        )

    def _update_composer_rating_leaderboard(self, *_args):
        if self.places_credits_stats is None:
            return
        leaderboard = self.places_credits_stats.get("rated_composers_leaderboard", {})
        threshold = self.composer_rating_tier.current_threshold()
        self.composer_highest_list.set_data(
            self._rating_rows_with_n(leaderboard.get("highest", {}).get(threshold, []))
        )
        self.composer_lowest_list.set_data(
            self._rating_rows_with_n(leaderboard.get("lowest", {}).get(threshold, []))
        )

    def _update_role_explorer(self, *_args):
        if self.places_credits_stats is None:
            return
        role = self.role_combo.currentText()
        stats = self.places_credits_stats

        prolific = stats.get("prolific_artist_by_role", {}).get(role, [])
        self.role_prolific_list.set_data([(name, count, None) for name, count in prolific])

        rated = stats.get("top_rated_artist_by_role", {}).get(role, {})
        highest_dict = rated.get("highest", {})
        lowest_dict = rated.get("lowest", {})
        non_empty = [
            t
            for t in set(highest_dict) | set(lowest_dict)
            if highest_dict.get(t) or lowest_dict.get(t)
        ]
        self.role_rating_tier.set_thresholds_available(
            non_empty or list(highest_dict.keys())
        )
        threshold = self.role_rating_tier.current_threshold()
        self.role_rated_highest_list.set_data(
            self._rating_rows_with_n(highest_dict.get(threshold, []))
        )
        self.role_rated_lowest_list.set_data(
            self._rating_rows_with_n(lowest_dict.get(threshold, []))
        )

    def load_audio_profile_data(self):
        """Load Audio Profile tab data (already fetched in self.audio_stats
        by the lazy AudioStatsWorker)."""
        stats = self.audio_stats
        if stats is None:
            return

        bpm = stats.get("bpm_distribution", {})
        bpm_key = "confident" if self.bpm_confidence_checkbox.isChecked() else "all"
        self.bpm_chart.set_data(bpm.get(bpm_key))

        key_dist = stats.get("key_distribution", {})
        key_key = "confident" if self.key_confidence_checkbox.isChecked() else "all"
        self.key_chart.set_data(key_dist.get(key_key))

        self.track_gain_chart.set_data(stats.get("track_gain_distribution"))

        quietest_loudest = stats.get("quietest_loudest", {})
        self.quietest_list.set_data(
            self._track_metric_rows(quietest_loudest.get("quietest", []))
        )
        self.loudest_list.set_data(
            self._track_metric_rows(quietest_loudest.get("loudest", []))
        )

        time_sig_dist = stats.get("time_signature_distribution", {})
        time_sig_key = (
            "confident"
            if self.time_signature_confidence_checkbox.isChecked()
            else "all"
        )
        self.time_signature_chart.set_data(time_sig_dist.get(time_sig_key))
        self.file_size_chart.set_data(stats.get("file_size_distribution"))

        self.instrumental_chart.set_data(stats.get("instrumental_distribution"))
        self.classical_chart.set_data(stats.get("classical_distribution"))

        self.load_dsp_metric(self.dsp_metric_combo.currentText())

    def load_dsp_metric(self, label: str):
        """Update the DSP histogram + top/bottom lists for the selected
        advanced-audio-property metric. All 16 metrics are already computed
        (see stats/audio.py), so switching the selector needs no new query."""
        stats = self.audio_stats
        if stats is None or not label:
            return

        distributions = stats.get("dsp_distributions", {})
        self.dsp_chart.set_data(distributions.get(label))

        top_bottom = stats.get("dsp_top_bottom", {}).get(label, {})
        self.dsp_top_list.set_data(self._track_metric_rows(top_bottom.get("top", [])))
        self.dsp_bottom_list.set_data(
            self._track_metric_rows(top_bottom.get("bottom", []))
        )

    def _track_metric_rows(self, rows):
        """Remap track_metric_top_bottom's (track_name, artist, value) rows
        into LeaderboardListWidget's (name, value, secondary_label) shape."""
        return [(name, value, artist) for name, artist, value in rows]

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def create_stat_label(self, text):
        """Create a consistent stat label."""
        label = QLabel(text)
        label.setObjectName("StatValueLabel")
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
            f'<span style="color: {_HIGHLIGHT_COLOR}; font-weight: bold;">{formatted_value}</span>'
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
        if seconds < HOUR:
            m = seconds // MINUTE
            s = seconds % MINUTE
            return f"{m}m {s}s"
        if seconds < DAY:
            h = seconds // HOUR
            m = (seconds % HOUR) // MINUTE
            return f"{h}h {m}m"
        if seconds < YEAR:
            d = seconds // DAY
            h = (seconds % DAY) // HOUR
            return f"{d}d {h}h"
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
        """If a background stats fetch is still in flight when the dialog
        is closed, detach its signals instead of blocking on it — the
        worker finishes on its own and this (soon-destroyed) dialog just
        won't hear about it.
        """
        for worker, slots in (
            (
                self.worker,
                [
                    (self.on_stats_loaded,),
                    (self.on_stats_error,),
                ],
            ),
            (
                self.influence_worker,
                [
                    (self.on_influence_stats_loaded,),
                    (self.on_influence_stats_error,),
                ],
            ),
            (
                self.audio_worker,
                [
                    (self.on_audio_stats_loaded,),
                    (self.on_audio_stats_error,),
                ],
            ),
            (
                self.genre_mood_worker,
                [
                    (self.on_genre_mood_stats_loaded,),
                    (self.on_genre_mood_stats_error,),
                ],
            ),
            (
                self.album_worker,
                [
                    (self.on_album_stats_loaded,),
                    (self.on_album_stats_error,),
                ],
            ),
            (
                self.artist_worker,
                [
                    (self.on_artist_stats_loaded,),
                    (self.on_artist_stats_error,),
                ],
            ),
            (
                self.places_credits_worker,
                [
                    (self.on_places_credits_stats_loaded,),
                    (self.on_places_credits_stats_error,),
                ],
            ),
            (
                self.lyrics_worker,
                [
                    (self.on_lyrics_stats_loaded,),
                    (self.on_lyrics_stats_error,),
                ],
            ),
        ):
            if worker is not None and worker.isRunning():
                with contextlib.suppress(TypeError, RuntimeError):
                    worker.finished.disconnect(slots[0][0])
                with contextlib.suppress(TypeError, RuntimeError):
                    worker.error.disconnect(slots[1][0])
        super().closeEvent(event)
