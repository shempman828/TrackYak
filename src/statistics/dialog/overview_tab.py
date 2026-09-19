"""Overview tab: headline tiles, library summary, averages, top performers."""

from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from src.statistics.dialog.shared import _hl, _recompute_bar
from src.statistics.widgets.stat_tile import StatTileWidget
from src.statistics.workers.influence_stats_worker import InfluenceStatsWorker


class OverviewTabMixin:
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
        self.highest_rated_artist_label = self.create_stat_label("Highest Rated Artist:")
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
            self.most_eclectic_tile.set_data(name, f"spans {bridges} communities")
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
            stats["total_tracks"] / stats["total_artists"] if stats["total_artists"] > 0 else 0
        )
        self.avg_tracks_artist_label.setText(
            f"Tracks per Artist: {self.format_stat_value(avg_tracks_per_artist)}"
        )

        avg_tracks_per_genre = (
            stats["total_tracks"] / stats["total_genres"] if stats["total_genres"] > 0 else 0
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
            self.common_birthdate_tile.set_data(birthdate["label"], f"{birthdate['count']} artists")
        else:
            self.common_birthdate_tile.set_data("N/A")
