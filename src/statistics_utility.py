from datetime import datetime

from sqlalchemy import case, func

from src.db_tables import (
    Album,
    Artist,
    Genre,
    Mood,
    MoodTrackAssociation,
    Track,
    TrackArtistRole,
    TrackGenre,
)
from src.logger_config import logger

# Valid rating range — values outside this are excluded from all rating calculations
RATING_MIN = 0.5
RATING_MAX = 10.0


class MusicStatistics:
    """Dedicated class for music library statistics and analytics"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_statistics(self):
        """Get all statistics in one efficient method call"""
        session = self.session_factory()
        try:
            stats = {}

            # Basic library counts
            stats.update(self._get_basic_counts(session))

            # Play statistics
            stats.update(self._get_play_statistics(session))

            # Rating statistics
            stats.update(self._get_rating_statistics(session))

            # Audio quality metrics
            audio_stats = self._get_audio_quality_stats(session)
            stats["audio_quality_stats"] = audio_stats

            # File format distribution
            stats["file_format_distribution"] = self.get_file_format_distribution()

            # Temporal statistics
            temporal_stats = self._get_temporal_statistics(session)
            stats["temporal_statistics"] = temporal_stats

            # Leaderboards — returns plain dicts (no ORM objects) to avoid
            # detached-instance errors when the session is closed.
            leaderboards_data = self._get_leaderboards(session)
            stats["leaderboards"] = leaderboards_data

            # Metadata completeness — 4 axes using is_fixed / is_complete flags
            completeness_stats = self._get_metadata_completeness(session)
            stats["metadata_completeness"] = completeness_stats
            overall_completeness = (
                sum(completeness_stats.values()) / len(completeness_stats)
                if completeness_stats
                else 0
            )
            stats["overall_metadata_completeness"] = round(overall_completeness, 1)

            # Ratings distribution
            ratings_dist = self.get_ratings_distribution()
            stats["ratings_distribution"] = ratings_dist

            return stats

        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Basic counts                                                        #
    # ------------------------------------------------------------------ #

    def _get_basic_counts(self, session):
        """Get basic entity counts"""
        track_count = session.query(func.count(Track.track_id)).scalar() or 0
        artist_count = session.query(func.count(Artist.artist_id)).scalar() or 0
        album_count = session.query(func.count(Album.album_id)).scalar() or 0
        genre_count = session.query(func.count(Genre.genre_id)).scalar() or 0
        mood_count = session.query(func.count(Mood.mood_id)).scalar() or 0

        return {
            "total_tracks": track_count,
            "total_artists": artist_count,
            "total_albums": album_count,
            "total_genres": genre_count,
            "total_moods": mood_count,
        }

    # ------------------------------------------------------------------ #
    #  Play statistics                                                     #
    # ------------------------------------------------------------------ #

    def _get_play_statistics(self, session):
        """Get play-related statistics.

        Total play time = sum(duration * play_count) for every track.
        This is done entirely in SQL so no Python loops over large libraries.
        """
        total_plays_query = func.coalesce(func.sum(Track.play_count), 0)
        total_play_time_query = func.coalesce(
            func.sum(Track.duration * func.coalesce(Track.play_count, 0)), 0
        )
        total_file_size_query = func.coalesce(func.sum(Track.file_size), 0)
        avg_plays_query = func.coalesce(func.avg(Track.play_count), 0)

        play_stats = session.query(
            total_plays_query.label("total_plays"),
            total_play_time_query.label("total_play_time"),
            total_file_size_query.label("total_file_size"),
            avg_plays_query.label("avg_plays_per_track"),
        ).one()

        total_duration = (
            session.query(func.coalesce(func.sum(Track.duration), 0)).scalar() or 0
        )

        return {
            "total_plays": play_stats.total_plays or 0,
            "total_play_time": play_stats.total_play_time or 0,
            "total_library_duration": total_duration,
            "total_file_size": play_stats.total_file_size or 0,
            "avg_plays_per_track": round(play_stats.avg_plays_per_track or 0, 1),
        }

    # ------------------------------------------------------------------ #
    #  Rating statistics                                                   #
    # ------------------------------------------------------------------ #

    def _valid_rating_filter(self):
        """Return a SQLAlchemy filter expression for valid ratings (0.5 – 10)."""
        return (
            Track.user_rating.isnot(None),
            Track.user_rating >= RATING_MIN,
            Track.user_rating <= RATING_MAX,
        )

    def _get_rating_statistics(self, session):
        """Get rating-related statistics, excluding out-of-range values."""
        rating_stats = (
            session.query(
                func.avg(Track.user_rating).label("avg_rating"),
                func.avg(
                    case((Track.play_count > 0, Track.user_rating), else_=None)
                ).label("avg_played_rating"),
                func.count(Track.track_id).label("rated_tracks"),
            )
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .one()
        )

        total_tracks = session.query(func.count(Track.track_id)).scalar()
        rating_completeness = (
            (rating_stats.rated_tracks / total_tracks * 100) if total_tracks > 0 else 0
        )

        return {
            "average_rating": round(rating_stats.avg_rating or 0, 2),
            "average_played_rating": round(rating_stats.avg_played_rating or 0, 2),
            "rating_completeness": round(rating_completeness, 1),
            "rated_tracks": rating_stats.rated_tracks or 0,
        }

    # ------------------------------------------------------------------ #
    #  Metadata completeness — 4 axes                                     #
    # ------------------------------------------------------------------ #

    def _get_metadata_completeness(self, session):
        """Calculate metadata completeness using is_fixed flags.

        Four axes:
          - tracks:  tracks with is_fixed == 1
          - artists: artists with is_fixed == 1
          - albums:  albums  with is_fixed == 1
          - total:   average of the three percentages above
        """
        total_tracks = session.query(func.count(Track.track_id)).scalar() or 0
        total_artists = session.query(func.count(Artist.artist_id)).scalar() or 0
        total_albums = session.query(func.count(Album.album_id)).scalar() or 0

        if total_tracks == 0 and total_artists == 0 and total_albums == 0:
            return {
                "tracks_complete": 0.0,
                "artists_complete": 0.0,
                "albums_complete": 0.0,
                "total_complete": 0.0,
            }

        fixed_tracks = (
            session.query(func.count(Track.track_id))
            .filter(Track.is_fixed == 1)
            .scalar()
            or 0
        )
        fixed_artists = (
            session.query(func.count(Artist.artist_id))
            .filter(Artist.is_fixed == 1)
            .scalar()
            or 0
        )
        fixed_albums = (
            session.query(func.count(Album.album_id))
            .filter(Album.is_fixed == 1)
            .scalar()
            or 0
        )

        tracks_pct = round(
            (fixed_tracks / total_tracks * 100) if total_tracks else 0, 1
        )
        artists_pct = round(
            (fixed_artists / total_artists * 100) if total_artists else 0, 1
        )
        albums_pct = round(
            (fixed_albums / total_albums * 100) if total_albums else 0, 1
        )
        total_pct = round((tracks_pct + artists_pct + albums_pct) / 3, 1)

        return {
            "tracks_complete": tracks_pct,
            "artists_complete": artists_pct,
            "albums_complete": albums_pct,
            "total_complete": total_pct,
        }

    # Keep the old public method name for any callers that use it directly.
    def get_metadata_completeness(self):
        """Public wrapper — returns the 4-axis completeness dict."""
        session = self.session_factory()
        try:
            return self._get_metadata_completeness(session)
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Audio quality                                                       #
    # ------------------------------------------------------------------ #

    def _get_audio_quality_stats(self, session):
        """Get audio quality statistics"""
        try:
            quality_stats = session.query(
                func.avg(Track.bit_rate).label("avg_bit_rate"),
                func.avg(Track.sample_rate).label("avg_sample_rate"),
                func.avg(Track.bit_depth).label("avg_bit_depth"),
                func.avg(Track.duration).label("avg_duration"),
                func.avg(Track.file_size).label("avg_file_size"),
            ).one()

            return {
                "average_bit_rate": (
                    round(quality_stats.avg_bit_rate, 0)
                    if quality_stats.avg_bit_rate
                    else None
                ),
                "average_sample_rate": (
                    round(quality_stats.avg_sample_rate, 0)
                    if quality_stats.avg_sample_rate
                    else None
                ),
                "average_bit_depth": (
                    round(quality_stats.avg_bit_depth, 1)
                    if quality_stats.avg_bit_depth
                    else None
                ),
                "average_duration": (
                    round(quality_stats.avg_duration, 1)
                    if quality_stats.avg_duration
                    else None
                ),
                "average_file_size": (
                    round(quality_stats.avg_file_size, 0)
                    if quality_stats.avg_file_size
                    else None
                ),
            }
        except Exception as e:
            logger.error(f"Error getting audio quality stats: {e}")
            return {}

    # ------------------------------------------------------------------ #
    #  File formats                                                        #
    # ------------------------------------------------------------------ #

    def get_file_format_distribution(self):
        """Get distribution of file formats"""
        session = self.session_factory()
        try:
            format_counts = (
                session.query(
                    Track.file_extension,
                    func.count(Track.track_id).label("count"),
                )
                .filter(Track.file_extension.isnot(None))
                .group_by(Track.file_extension)
                .order_by(func.count(Track.track_id).desc())
                .all()
            )
            return {fmt: count for fmt, count in format_counts}
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Temporal statistics                                                 #
    # ------------------------------------------------------------------ #

    def _get_temporal_statistics(self, session):
        """Get time-based statistics"""
        try:
            tracks_with_years = (
                session.query(
                    case(
                        (Track.recorded_year.isnot(None), Track.recorded_year),
                        else_=Album.release_year,
                    ).label("effective_release_year"),
                    func.count(Track.track_id).label("track_count"),
                )
                .outerjoin(Album, Track.album_id == Album.album_id)
                .group_by("effective_release_year")
                .subquery()
            )

            decades = (
                session.query(
                    (tracks_with_years.c.effective_release_year / 10 * 10).label(
                        "decade"
                    ),
                    func.sum(tracks_with_years.c.track_count).label("count"),
                )
                .filter(tracks_with_years.c.effective_release_year.isnot(None))
                .group_by("decade")
                .order_by("decade")
                .all()
            )

            release_years = (
                session.query(
                    tracks_with_years.c.effective_release_year,
                    tracks_with_years.c.track_count,
                )
                .order_by(tracks_with_years.c.effective_release_year)
                .all()
            )

            recent_tracks = (
                session.query(Track.track_name, Track.date_added)
                .filter(Track.date_added.isnot(None))
                .order_by(Track.date_added.desc())
                .limit(10)
                .all()
            )

            years_with_tracks = (
                session.query(tracks_with_years.c.effective_release_year)
                .filter(tracks_with_years.c.effective_release_year.isnot(None))
                .distinct()
                .count()
            )

            total_tracks = session.query(func.count(Track.track_id)).scalar()
            avg_tracks_per_year = (
                total_tracks / years_with_tracks if years_with_tracks > 0 else 0
            )

            return {
                "decade_breakdown": {decade: count for decade, count in decades},
                "release_years": {
                    year: count for year, count in release_years if year is not None
                },
                "recently_added": [(name, added) for name, added in recent_tracks],
                "avg_tracks_per_year": round(avg_tracks_per_year, 1),
                "unique_years_count": years_with_tracks,
            }
        except Exception as e:
            logger.error(f"Error getting temporal statistics: {e}")
            return {}

    # ------------------------------------------------------------------ #
    #  Leaderboards — all data extracted to plain types before session    #
    #  closes to avoid detached-instance errors.                          #
    # ------------------------------------------------------------------ #

    def _get_leaderboards(self, session):
        """Get top performers. Returns plain dicts/tuples — no ORM objects."""
        leaderboards = {}
        leaderboards["top_artists"] = self._get_top_artists(session)
        leaderboards["top_genres"] = self._get_top_genres(session)
        leaderboards["top_moods"] = self._get_top_moods(session)
        leaderboards["top_tracks"] = self._get_top_tracks(session)

        rating_leaderboards = self._get_rating_leaderboards(session)
        leaderboards.update(rating_leaderboards)

        return leaderboards

    def _get_top_artists(self, session, limit=10):
        """Top artists by play count — returns list of (name, plays) tuples."""
        rows = (
            session.query(
                Artist.artist_name,
                func.coalesce(func.sum(Track.play_count), 0).label("total_plays"),
            )
            .select_from(Artist)
            .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .group_by(Artist.artist_id, Artist.artist_name)
            .order_by(func.coalesce(func.sum(Track.play_count), 0).desc())
            .limit(limit)
            .all()
        )
        return [(name, plays) for name, plays in rows]

    def _get_top_genres(self, session, limit=10):
        """Top genres by play count — returns list of (name, plays) tuples."""
        rows = (
            session.query(
                Genre.genre_name,
                func.coalesce(func.sum(Track.play_count), 0).label("total_plays"),
            )
            .select_from(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .group_by(Genre.genre_id, Genre.genre_name)
            .order_by(func.coalesce(func.sum(Track.play_count), 0).desc())
            .limit(limit)
            .all()
        )
        return [(name, plays) for name, plays in rows]

    def _get_top_moods(self, session, limit=10):
        """Top moods by play count — returns list of (name, plays) tuples."""
        rows = (
            session.query(
                Mood.mood_name,
                func.coalesce(func.sum(Track.play_count), 0).label("total_plays"),
            )
            .select_from(Mood)
            .join(MoodTrackAssociation, Mood.mood_id == MoodTrackAssociation.mood_id)
            .join(Track, MoodTrackAssociation.track_id == Track.track_id)
            .group_by(Mood.mood_id, Mood.mood_name)
            .order_by(func.coalesce(func.sum(Track.play_count), 0).desc())
            .limit(limit)
            .all()
        )
        return [(name, plays) for name, plays in rows]

    def _get_top_tracks(self, session, limit=10):
        """Most played tracks — returns list of (name, play_count) tuples."""
        rows = (
            session.query(
                Track.track_name,
                func.coalesce(Track.play_count, 0).label("play_count"),
            )
            .order_by(func.coalesce(Track.play_count, 0).desc())
            .limit(limit)
            .all()
        )
        return [(name, plays) for name, plays in rows]

    def _get_rating_leaderboards(self, session):
        """Top performers by rating.

        Rules:
         - Only ratings in the valid range (0.5 – 10) are considered.
         - Artists need at least 3 rated tracks (no minimum play count —
           the old play_count > 5 filter was too strict and broke the result).
         - Albums and genres need at least 3 rated tracks.
        Returns plain (name, avg_rating) tuples, not ORM objects.
        """
        # Highest rated artists
        highest_rated_artists = (
            session.query(
                Artist.artist_name,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Artist)
            .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Artist.artist_id, Artist.artist_name)
            .having(func.count(Track.track_id) >= 3)
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Highest rated albums
        highest_rated_albums = (
            session.query(
                Album.album_name,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Album)
            .join(Track, Album.album_id == Track.album_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Album.album_id, Album.album_name)
            .having(func.count(Track.track_id) >= 3)
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Highest rated genres
        highest_rated_genres = (
            session.query(
                Genre.genre_name,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Genre.genre_id, Genre.genre_name)
            .having(func.count(Track.track_id) >= 5)
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Lowest rated genres
        lowest_rated_genres = (
            session.query(
                Genre.genre_name,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Genre.genre_id, Genre.genre_name)
            .having(func.count(Track.track_id) >= 5)
            .order_by(func.avg(Track.user_rating).asc())
            .limit(5)
            .all()
        )

        return {
            # Each entry is (name_string, avg_rating_float)
            "highest_rated_artists": [
                (n, round(r, 2)) for n, r in highest_rated_artists
            ],
            "highest_rated_albums": [(n, round(r, 2)) for n, r in highest_rated_albums],
            "highest_rated_genres": [(n, round(r, 2)) for n, r in highest_rated_genres],
            "lowest_rated_genres": [(n, round(r, 2)) for n, r in lowest_rated_genres],
        }

    # ------------------------------------------------------------------ #
    #  Ratings distribution                                               #
    # ------------------------------------------------------------------ #

    def get_ratings_distribution(self):
        """Get distribution of tracks across rating values, filtered to valid range."""
        session = self.session_factory()
        try:
            ratings_distribution = (
                session.query(
                    Track.user_rating, func.count(Track.track_id).label("track_count")
                )
                .filter(
                    Track.user_rating.isnot(None),
                    Track.user_rating >= RATING_MIN,
                    Track.user_rating <= RATING_MAX,
                )
                .group_by(Track.user_rating)
                .order_by(Track.user_rating)
                .all()
            )

            distribution_dict = {}
            for rating, count in ratings_distribution:
                if rating is not None:
                    distribution_dict[float(rating)] = count

            total_rated_tracks = (
                sum(distribution_dict.values()) if distribution_dict else 0
            )
            unrated_tracks = (
                session.query(func.count(Track.track_id))
                .filter(Track.user_rating.is_(None))
                .scalar()
                or 0
            )
            total_tracks = total_rated_tracks + unrated_tracks

            avg_rating_result = (
                session.query(func.avg(Track.user_rating))
                .filter(
                    Track.user_rating.isnot(None),
                    Track.user_rating >= RATING_MIN,
                    Track.user_rating <= RATING_MAX,
                )
                .scalar()
            )

            return {
                "distribution": distribution_dict,
                "total_rated": total_rated_tracks,
                "total_unrated": unrated_tracks,
                "total_tracks": total_tracks,
                "average_rating": round(avg_rating_result, 2)
                if avg_rating_result
                else None,
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  On This Day                                                         #
    # ------------------------------------------------------------------ #

    def get_on_this_day(self):
        """Get music events for today's date. Returns plain data, no ORM objects."""
        session = self.session_factory()
        try:
            today = datetime.now()
            month, day = today.month, today.day

            albums_released = [
                {"name": a.album_name, "year": a.release_year}
                for a in session.query(Album)
                .filter(Album.release_month == month, Album.release_day == day)
                .all()
            ]
            artists_born = [
                {"name": a.artist_name, "year": a.begin_year}
                for a in session.query(Artist)
                .filter(Artist.begin_month == month, Artist.begin_day == day)
                .all()
            ]
            artists_died = [
                {"name": a.artist_name, "year": a.end_year}
                for a in session.query(Artist)
                .filter(Artist.end_month == month, Artist.end_day == day)
                .all()
            ]
            tracks_recorded = [
                {"name": t.track_name, "year": t.recorded_year}
                for t in session.query(Track)
                .filter(Track.recorded_month == month, Track.recorded_day == day)
                .all()
            ]
            tracks_composed = [
                {"name": t.track_name, "year": t.composed_year}
                for t in session.query(Track)
                .filter(Track.composed_month == month, Track.composed_day == day)
                .all()
            ]

            return {
                "albums_released": albums_released,
                "artists_born": artists_born,
                "artists_died": artists_died,
                "tracks_recorded": tracks_recorded,
                "tracks_composed": tracks_composed,
            }
        finally:
            session.close()
