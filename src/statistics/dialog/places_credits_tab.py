"""Places & Credits tab: places/countries/publishers/composers ratings, role explorer."""

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.statistics.dialog.shared import _recompute_bar
from src.statistics.widgets.leaderboard_list import LeaderboardListWidget
from src.statistics.widgets.threshold_tier_widget import ThresholdTierWidget
from src.statistics.workers.places_credits_stats_worker import PlacesCreditsStatsWorker


class PlacesCreditsTabMixin:
    def create_places_credits_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        places_credits_recompute_bar, self.places_credits_recompute_button = _recompute_bar(
            self._recompute_places_credits_stats
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

        countries_group = QGroupBox("Highest / Lowest Rated Countries (recursive rollup)")
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
        self.publisher_rating_tier.tier_changed.connect(self._update_publisher_rating_leaderboard)
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
        self.composer_rating_tier.tier_changed.connect(self._update_composer_rating_leaderboard)
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

    def _load_places_credits_stats(self):
        """Lazy-load the Places & Credits tab's content. Runs once per
        dialog session -- the heaviest of the per-tab workers (recursive
        country/artist-by-country rollups plus a per-role leaderboard loop)."""
        if self.places_credits_worker is not None and self.places_credits_worker.isRunning():
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
        self.place_rating_tier.set_thresholds_available(non_empty or list(highest_dict.keys()))
        self._update_place_rating_leaderboard()

        countries = stats.get("rated_countries", {})
        self.country_highest_list.set_data(self._rating_rows_with_n(countries.get("highest", [])))
        self.country_lowest_list.set_data(self._rating_rows_with_n(countries.get("lowest", [])))

        highest_by_country = stats.get("highest_rated_artist_by_country", {})
        country_rows = sorted(
            [(country, rating, artist) for country, (artist, rating) in highest_by_country.items()],
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
            [(name, count, None) for name, count in role_counts.get("most_distinct_roles", [])]
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
            self._rows_with_note(leaderboard.get("highest", {}).get(threshold, []), "albums")
        )
        self.publisher_lowest_list.set_data(
            self._rows_with_note(leaderboard.get("lowest", {}).get(threshold, []), "albums")
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
        self.role_rating_tier.set_thresholds_available(non_empty or list(highest_dict.keys()))
        threshold = self.role_rating_tier.current_threshold()
        self.role_rated_highest_list.set_data(
            self._rating_rows_with_n(highest_dict.get(threshold, []))
        )
        self.role_rated_lowest_list.set_data(
            self._rating_rows_with_n(lowest_dict.get(threshold, []))
        )
