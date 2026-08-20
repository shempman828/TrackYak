"""
stats/genres_moods.py

GenreMoodStats: power-of-10 rating leaderboards for genres, most niche
genre (deepest nested chain with tracks), top/bottom 5 genres by track
count, outlier-controlled mood ratings, and most/least played mood.
"""

from sqlalchemy import func

from src.db.db_tables import Genre, Mood, MoodTrackAssociation, Track, TrackGenre
from src.statistics.stats.helpers import (
    RATING_MAX,
    RATING_MIN,
    outlier_controlled_average,
    threshold_leaderboard,
)


class GenreMoodStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_genre_mood_stats(self):
        session = self.session_factory()
        try:
            return {
                "rated_genres_leaderboard": self._rated_genres_leaderboard(session),
                "most_niche_genre": self._most_niche_genre(session),
                "genres_by_track_count": self._genres_by_track_count(session),
                "mood_ratings_outlier_controlled": self._mood_ratings(session),
                "mood_play_counts": self._mood_play_counts(session),
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Highest/lowest rated genres (power-of-10)                          #
    # ------------------------------------------------------------------ #

    def _rated_genres_leaderboard(self, session):
        base_query = (
            session.query(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
        )
        return {
            "highest": threshold_leaderboard(
                session,
                base_query,
                Genre.genre_id,
                Genre.genre_name,
                Track.user_rating,
                ascending=False,
            ),
            "lowest": threshold_leaderboard(
                session,
                base_query,
                Genre.genre_id,
                Genre.genre_name,
                Track.user_rating,
                ascending=True,
            ),
        }

    # ------------------------------------------------------------------ #
    #  Most niche genre (deepest nested chain that actually has tracks)   #
    # ------------------------------------------------------------------ #

    def _most_niche_genre(self, session):
        genres_with_tracks = (
            session.query(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .distinct()
            .all()
        )
        if not genres_with_tracks:
            return None
        deepest = max(genres_with_tracks, key=lambda g: g.depth)
        return {
            "name": deepest.genre_name,
            "depth": deepest.depth,
            "path": deepest.full_genre_path,
        }

    # ------------------------------------------------------------------ #
    #  Top/bottom 5 genres by track count                                 #
    # ------------------------------------------------------------------ #

    def _genres_by_track_count(self, session, limit=5):
        rows = (
            session.query(Genre.genre_name, func.count(TrackGenre.track_id))
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .group_by(Genre.genre_id, Genre.genre_name)
            .having(func.count(TrackGenre.track_id) > 0)
            .all()
        )
        rows.sort(key=lambda r: r[1], reverse=True)
        return {
            "top": [(name, count) for name, count in rows[:limit]],
            "bottom": [(name, count) for name, count in rows[-limit:][::-1]],
        }

    # ------------------------------------------------------------------ #
    #  Highest/lowest rated mood (outlier-controlled)                     #
    # ------------------------------------------------------------------ #

    def _mood_ratings(self, session):
        moods = session.query(Mood).all()
        results = []
        for mood in moods:
            ratings = [
                t.user_rating
                for t in mood.tracks
                if t.user_rating is not None
                and RATING_MIN <= t.user_rating <= RATING_MAX
            ]
            avg = outlier_controlled_average(ratings)
            if avg is not None:
                results.append((mood.mood_name, round(avg, 2), len(ratings)))

        results.sort(key=lambda r: r[1], reverse=True)
        return {
            "highest": results[:5],
            "lowest": results[-5:][::-1] if results else [],
        }

    # ------------------------------------------------------------------ #
    #  Most / least played mood                                           #
    # ------------------------------------------------------------------ #

    def _mood_play_counts(self, session, limit=5):
        rows = (
            session.query(
                Mood.mood_name,
                func.coalesce(func.sum(Track.play_count), 0).label("plays"),
            )
            .select_from(Mood)
            .join(MoodTrackAssociation, Mood.mood_id == MoodTrackAssociation.mood_id)
            .join(Track, MoodTrackAssociation.track_id == Track.track_id)
            .group_by(Mood.mood_id, Mood.mood_name)
            .order_by(func.coalesce(func.sum(Track.play_count), 0).desc())
            .all()
        )
        return {
            "most_played": [(name, plays) for name, plays in rows[:limit]],
            "least_played": [(name, plays) for name, plays in rows[-limit:][::-1]],
        }
