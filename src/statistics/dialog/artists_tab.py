"""Artists tab: top artists, dates, artist type/gender, lifespan."""

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.statistics.dialog.shared import _lifespan_detail, _placeholder_label, _recompute_bar
from src.statistics.widgets.bar_distribution_chart import BarDistributionChart
from src.statistics.widgets.leaderboard_list import LeaderboardListWidget
from src.statistics.widgets.stat_tile import StatTileWidget
from src.statistics.widgets.threshold_tier_widget import ThresholdTierWidget
from src.statistics.workers.artist_stats_worker import ArtistStatsWorker


class ArtistsTabMixin:
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
        for tile in [self.longest_lived_tile, self.shortest_lived_tile, self.youngest_artist_tile]:
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

    def load_artists_data(self):
        """Load artists tab data."""
        leaderboards = self.stats.get("leaderboards", {})
        top_artists = leaderboards.get("top_artists", [])
        self.top_artists_list.set_data([(name, plays, None) for name, plays in top_artists])

        deathdate = self.stats.get("most_common_deathdate")
        if deathdate:
            self.common_deathdate_tile.set_data(deathdate["label"], f"{deathdate['count']} artists")
        else:
            self.common_deathdate_tile.set_data("N/A")

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
            [(type_name, rating, artist) for type_name, (artist, rating) in best_by_type.items()],
            key=lambda r: r[1],
            reverse=True,
        )
        self.artist_type_best_list.set_data(type_rows)

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
            self.youngest_artist_tile.set_data(youngest["name"], f"born {youngest['begin_year']}")
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
        self.gender_tier.set_thresholds_available(non_empty or list(highest_dict.keys()))
        threshold = self.gender_tier.current_threshold()
        self.gender_highest_list.set_data(self._rating_rows_with_n(highest_dict.get(threshold, [])))
        self.gender_lowest_list.set_data(self._rating_rows_with_n(lowest_dict.get(threshold, [])))
