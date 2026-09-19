"""Music library statistics dialog.

Composes the per-tab mixins in src.statistics.dialog into the full
MusicStatsDialog; each tab's UI building and data loading lives in its own
module there (overview_tab.py, artists_tab.py, ...).
"""

import contextlib
from typing import Any

from PySide6.QtWidgets import QDialog, QTabWidget, QVBoxLayout

from src.foundation.logger_config import logger
from src.statistics.dialog.albums_tab import AlbumsTabMixin
from src.statistics.dialog.artists_tab import ArtistsTabMixin
from src.statistics.dialog.audio_profile_tab import AudioProfileTabMixin
from src.statistics.dialog.formatting_mixin import FormattingMixin
from src.statistics.dialog.genres_moods_tab import GenresMoodsTabMixin
from src.statistics.dialog.library_health_tab import LibraryHealthTabMixin
from src.statistics.dialog.lyrics_tab import LyricsTabMixin
from src.statistics.dialog.overview_tab import OverviewTabMixin
from src.statistics.dialog.places_credits_tab import PlacesCreditsTabMixin
from src.statistics.dialog.shared import StatisticsWorker

__all__ = ["MusicStatsDialog"]


class MusicStatsDialog(
    FormattingMixin,
    OverviewTabMixin,
    LibraryHealthTabMixin,
    ArtistsTabMixin,
    AlbumsTabMixin,
    GenresMoodsTabMixin,
    PlacesCreditsTabMixin,
    AudioProfileTabMixin,
    LyricsTabMixin,
    QDialog,
):
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

    def on_stats_loaded(self, stats):
        """Populate the UI once the background worker has fetched the stats."""
        self.stats = stats
        try:
            self.load_overview_data()
            self.load_library_health_data()
            self.load_artists_data()
            self.load_albums_data()
            self.load_genres_moods_data()
            self.load_audio_quality_labels()
        except (KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Error updating statistics UI: {e}")

    def on_stats_error(self, message):
        logger.error(f"Statistics background fetch failed: {message}")

    def closeEvent(self, event):
        """If a background stats fetch is still in flight when the dialog
        is closed, detach its signals instead of blocking on it — the
        worker finishes on its own and this (soon-destroyed) dialog just
        won't hear about it.
        """
        for worker, slots in (
            (self.worker, [(self.on_stats_loaded,), (self.on_stats_error,)]),
            (
                self.influence_worker,
                [(self.on_influence_stats_loaded,), (self.on_influence_stats_error,)],
            ),
            (self.audio_worker, [(self.on_audio_stats_loaded,), (self.on_audio_stats_error,)]),
            (
                self.genre_mood_worker,
                [(self.on_genre_mood_stats_loaded,), (self.on_genre_mood_stats_error,)],
            ),
            (self.album_worker, [(self.on_album_stats_loaded,), (self.on_album_stats_error,)]),
            (self.artist_worker, [(self.on_artist_stats_loaded,), (self.on_artist_stats_error,)]),
            (
                self.places_credits_worker,
                [(self.on_places_credits_stats_loaded,), (self.on_places_credits_stats_error,)],
            ),
            (self.lyrics_worker, [(self.on_lyrics_stats_loaded,), (self.on_lyrics_stats_error,)]),
        ):
            if worker is not None and worker.isRunning():
                with contextlib.suppress(TypeError, RuntimeError):
                    worker.finished.disconnect(slots[0][0])
                with contextlib.suppress(TypeError, RuntimeError):
                    worker.error.disconnect(slots[1][0])
        super().closeEvent(event)
