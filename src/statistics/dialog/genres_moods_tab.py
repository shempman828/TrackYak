"""Genres & Moods tab: top genres/moods, rated leaderboards, niche genre, representative tracks."""

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
from src.statistics.widgets.stat_tile import StatTileWidget
from src.statistics.widgets.threshold_tier_widget import ThresholdTierWidget
from src.statistics.workers.genre_mood_stats_worker import GenreMoodStatsWorker


class GenresMoodsTabMixin:
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
        self.genre_rating_tier.tier_changed.connect(self._update_rated_genres_leaderboard)
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

    def load_genres_moods_data(self):
        """Load genres and moods tab data."""
        leaderboards = self.stats.get("leaderboards", {})

        top_genres = leaderboards.get("top_genres", [])
        self.top_genres_list.set_data([(name, plays, None) for name, plays in top_genres])

        top_moods = leaderboards.get("top_moods", [])
        self.top_moods_list.set_data([(name, plays, None) for name, plays in top_moods])

        highest_rated_genres = leaderboards.get("highest_rated_genres", [])
        self.highest_rated_genres_list.set_data(
            [(name, rating, None) for name, rating in highest_rated_genres]
        )

        lowest_rated_genres = leaderboards.get("lowest_rated_genres", [])
        self.lowest_rated_genres_list.set_data(
            [(name, rating, None) for name, rating in lowest_rated_genres]
        )

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
        self.genre_rating_tier.set_thresholds_available(non_empty or list(highest_dict.keys()))
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
            [(name, plays, None) for name, plays in mood_plays.get("least_played", [])]
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
