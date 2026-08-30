"""
statistics_utility.py

MusicStatistics: thin facade composing the domain-specific stat classes
under src/statistics/stats/. Mirrors MusicController's composition pattern
(controller.get/.add/.update/...) rather than one ever-growing flat class.

get_comprehensive_statistics() keeps its pre-existing name/shape (a single
dict) since that's what StatisticsWorker calls once when the dialog opens;
it delegates to BasicStats, which owns all the cheap single-table/
single-join aggregates, merged with DateStats' equally-cheap indexed
GROUP BYs. Heavier per-domain stats (recursive place/country rollups,
lyrics text analysis, the Audio Profile distributions) are called directly
via their own sub-service (self.places_credits, self.lyrics, self.audio,
...) from separate lazy per-tab workers instead of being folded into this
payload.
"""

from src.statistics.stats.albums import AlbumStats
from src.statistics.stats.artists import ArtistStats
from src.statistics.stats.audio import AudioStats
from src.statistics.stats.basic import BasicStats
from src.statistics.stats.dates import DateStats
from src.statistics.stats.genres_moods import GenreMoodStats
from src.statistics.stats.helpers import RATING_MAX, RATING_MIN
from src.statistics.stats.lyrics import LyricsStats
from src.statistics.stats.places_credits import PlacesCreditsStats

# Re-exported for backward compatibility with any existing import of these
# names from this module; the canonical definition now lives in stats/helpers.py.
__all__ = ["MusicStatistics", "RATING_MIN", "RATING_MAX"]


class MusicStatistics:
    """Dedicated facade for music library statistics and analytics."""

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.basic = BasicStats(session_factory)
        self.dates = DateStats(session_factory)
        self.artists = ArtistStats(session_factory)
        self.albums = AlbumStats(session_factory)
        self.genres_moods = GenreMoodStats(session_factory)
        self.places_credits = PlacesCreditsStats(session_factory)
        self.audio = AudioStats(session_factory)
        self.lyrics = LyricsStats(session_factory)

    def get_comprehensive_statistics(self):
        """Get all cheap statistics (loaded once when the dialog opens) in
        one call."""
        stats = self.basic.get_comprehensive_statistics()
        stats.update(self.dates.get_comprehensive_date_stats())
        return stats

    def get_on_this_day(self):
        return self.basic.get_on_this_day()
