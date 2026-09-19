"""Albums tab: highest rated, release dates/countries, sales, genre diversity."""

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from src.statistics.dialog.shared import _recompute_bar
from src.statistics.widgets.bar_distribution_chart import BarDistributionChart
from src.statistics.widgets.histogram_chart import HistogramChart
from src.statistics.widgets.leaderboard_list import LeaderboardListWidget
from src.statistics.widgets.stat_tile import StatTileWidget
from src.statistics.widgets.threshold_tier_widget import ThresholdTierWidget
from src.statistics.widgets.year_time_series_chart import YearTimeSeriesChart
from src.statistics.workers.album_stats_worker import AlbumStatsWorker


class AlbumsTabMixin:
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
        self.album_rating_tier.tier_changed.connect(self._update_album_rating_leaderboard)
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

    def load_albums_data(self):
        """Load albums tab data."""
        leaderboards = self.stats.get("leaderboards", {})
        top_albums = leaderboards.get("highest_rated_albums", [])
        self.top_albums_list.set_data([(name, rating, None) for name, rating in top_albums])

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

    def load_album_phase3_data(self):
        """Load the Albums tab's Phase-3 content (already fetched in
        self.album_stats by the lazy AlbumStatsWorker)."""
        stats = self.album_stats
        if stats is None:
            return

        leaderboard = stats.get("rating_by_track_count", {})
        non_empty = [t for t, rows in leaderboard.items() if rows]
        self.album_rating_tier.set_thresholds_available(non_empty or list(leaderboard.keys()))
        self._update_album_rating_leaderboard()

        self.release_country_chart.set_data(stats.get("release_country_distribution"))

        highest_by_country = stats.get("highest_rated_album_by_country", {})
        country_rows = sorted(
            [(country, rating, album) for country, (album, rating) in highest_by_country.items()],
            key=lambda r: r[1],
            reverse=True,
        )[:10]
        self.highest_rated_by_country_list.set_data(country_rows)

        self.sales_chart.set_data(stats.get("sales_distribution"))

        top_bottom_selling = stats.get("top_bottom_selling_albums", {})
        self.top_selling_list.set_data(
            [(name, sales, artist) for name, artist, sales in top_bottom_selling.get("top", [])]
        )
        self.bottom_selling_list.set_data(
            [(name, sales, artist) for name, artist, sales in top_bottom_selling.get("bottom", [])]
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
        self.album_rating_list.set_data(self._rating_rows_with_n(leaderboard.get(threshold, [])))
