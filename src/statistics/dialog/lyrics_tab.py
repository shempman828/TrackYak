"""Lyrics tab: word cloud and rating-skewed word lists."""

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from src.statistics.dialog.shared import _recompute_bar
from src.statistics.widgets.word_cloud_widget import WordCloudWidget
from src.statistics.workers.lyrics_stats_worker import LyricsStatsWorker


class LyricsTabMixin:
    def create_lyrics_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        lyrics_recompute_bar, self.lyrics_recompute_button = _recompute_bar(
            self._recompute_lyrics_stats
        )
        layout.addLayout(lyrics_recompute_bar)

        cloud_group = QGroupBox("Word Cloud (words appearing in 5+ distinct tracks)")
        cloud_layout = QVBoxLayout(cloud_group)
        self.lyrics_word_cloud = WordCloudWidget()
        cloud_layout.addWidget(self.lyrics_word_cloud)
        layout.addWidget(cloud_group)

        weighted_group = QGroupBox("Words Skewed Toward Higher / Lower Rated Tracks")
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
