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


class MusicStatistics:
    """Dedicated class for music library statistics and analytics"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_statistics(self):
        """Get all statistics in one efficient method call with debugging"""
        session = self.session_factory()
        try:
            logger.info("=== STARTING COMPREHENSIVE STATISTICS ===")
            stats = {}

            # Basic library counts
            logger.info("Getting basic counts...")
            stats.update(self._get_basic_counts(session))

            # Play statistics
            logger.info("Getting play statistics...")
            stats.update(self._get_play_statistics(session))

            # Rating statistics
            logger.info("Getting rating statistics...")
            stats.update(self._get_rating_statistics(session))

            # Audio quality metrics - nest under audio_quality_stats key
            logger.info("Getting audio quality statistics...")
            audio_stats = self._get_audio_quality_stats(session)
            stats["audio_quality_stats"] = audio_stats

            # File format distribution
            logger.info("Getting file format distribution...")
            stats["file_format_distribution"] = self.get_file_format_distribution()

            # Temporal statistics - nest under temporal_statistics key
            logger.info("Getting temporal statistics...")
            temporal_stats = self._get_temporal_statistics(session)
            stats["temporal_statistics"] = temporal_stats

            # Leaderboards - get all leaderboard data
            logger.info("Getting leaderboards...")
            leaderboards_data = self._get_leaderboards(session)
            stats["leaderboards"] = leaderboards_data

            # Metadata completeness - include in main stats
            logger.info("Getting metadata completeness...")
            completeness_stats = self.get_metadata_completeness()
            stats["metadata_completeness"] = completeness_stats
            # Calculate overall completeness for progress bar
            overall_completeness = (
                sum(completeness_stats.values()) / len(completeness_stats)
                if completeness_stats
                else 0
            )
            stats["overall_metadata_completeness"] = round(overall_completeness, 1)

            # Ratings distribution - include summary in main stats
            logger.info("Getting ratings distribution...")
            ratings_dist = self.get_ratings_distribution()
            stats["ratings_distribution"] = ratings_dist

            logger.info("=== FINAL STATISTICS RESULT ===")
            for key, value in stats.items():
                if key not in [
                    "leaderboards",
                    "audio_quality_stats",
                    "temporal_statistics",
                    "ratings_distribution",
                ]:
                    logger.info(f"{key}: {value}")

            logger.info("=== END COMPREHENSIVE STATISTICS ===")
            return stats

        finally:
            session.close()

    def _get_basic_counts(self, session):
        """Get basic entity counts with proper joins to avoid cartesian product"""
        # Query each count separately to avoid cartesian product
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

    def _get_play_statistics(self, session):
        """Get play-related statistics with debugging"""
        logger.info("=== DEBUG _get_play_statistics ===")

        # First, let's check what data we actually have in the database
        sample_tracks = (
            session.query(
                Track.track_id,
                Track.track_name,
                Track.duration,
                Track.play_count,
                Track.file_size,
            )
            .limit(5)
            .all()
        )

        logger.info("Sample tracks data:")
        for track in sample_tracks:
            logger.info(
                f"  Track: {track.track_name}, Duration: {track.duration}, "
                f"Plays: {track.play_count}, File Size: {track.file_size}"
            )

        # Check totals
        total_tracks = session.query(func.count(Track.track_id)).scalar()
        tracks_with_duration = (
            session.query(func.count(Track.track_id))
            .filter(Track.duration.isnot(None))
            .scalar()
        )
        tracks_with_plays = (
            session.query(func.count(Track.track_id))
            .filter(Track.play_count.isnot(None))
            .scalar()
        )
        tracks_with_filesize = (
            session.query(func.count(Track.track_id))
            .filter(Track.file_size.isnot(None))
            .scalar()
        )

        logger.info(f"Total tracks: {total_tracks}")
        logger.info(f"Tracks with duration: {tracks_with_duration}")
        logger.info(f"Tracks with play count: {tracks_with_plays}")
        logger.info(f"Tracks with file size: {tracks_with_filesize}")

        # Debug the actual queries
        total_plays_query = func.coalesce(func.sum(Track.play_count), 0)
        total_play_time_query = func.coalesce(
            func.sum(Track.duration * func.coalesce(Track.play_count, 0)), 0
        )
        total_file_size_query = func.coalesce(func.sum(Track.file_size), 0)
        avg_plays_query = func.coalesce(func.avg(Track.play_count), 0)

        logger.info("Query components:")
        logger.info(f"  Total plays query: {total_plays_query}")
        logger.info(f"  Total play time query: {total_play_time_query}")
        logger.info(f"  Total file size query: {total_file_size_query}")
        logger.info(f"  Avg plays query: {avg_plays_query}")

        # Execute the main query
        play_stats = session.query(
            total_plays_query.label("total_plays"),
            total_play_time_query.label("total_play_time"),
            total_file_size_query.label("total_file_size"),
            avg_plays_query.label("avg_plays_per_track"),
        ).one()

        logger.info("Raw query results:")
        logger.info(f"  Total plays: {play_stats.total_plays}")
        logger.info(f"  Total play time: {play_stats.total_play_time}")
        logger.info(f"  Total file size: {play_stats.total_file_size}")
        logger.info(f"  Avg plays per track: {play_stats.avg_plays_per_track}")

        # Calculate total library duration
        total_duration = (
            session.query(func.coalesce(func.sum(Track.duration), 0)).scalar() or 0
        )
        logger.info(f"Total library duration: {total_duration}")

        result = {
            "total_plays": play_stats.total_plays or 0,
            "total_play_time": play_stats.total_play_time or 0,
            "total_library_duration": total_duration,
            "total_file_size": play_stats.total_file_size or 0,
            "avg_plays_per_track": round(play_stats.avg_plays_per_track or 0, 1),
        }

        logger.info("Final play statistics result:")
        for key, value in result.items():
            logger.info(f"  {key}: {value}")

        logger.info("=== END DEBUG _get_play_statistics ===")
        return result

    def _get_rating_statistics(self, session):
        """Get rating-related statistics"""
        rating_stats = (
            session.query(
                func.avg(Track.user_rating).label("avg_rating"),
                func.avg(
                    case((Track.play_count > 0, Track.user_rating), else_=None)
                ).label("avg_played_rating"),
                func.count(Track.track_id).label("rated_tracks"),
            )
            .filter(Track.user_rating.isnot(None))
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

    def _get_basic_counts(self, session):
        """Get basic entity counts with debugging"""
        logger.info("=== DEBUG _get_basic_counts ===")

        # Query each count separately to avoid cartesian product
        track_count = session.query(func.count(Track.track_id)).scalar() or 0
        artist_count = session.query(func.count(Artist.artist_id)).scalar() or 0
        album_count = session.query(func.count(Album.album_id)).scalar() or 0
        genre_count = session.query(func.count(Genre.genre_id)).scalar() or 0
        mood_count = session.query(func.count(Mood.mood_id)).scalar() or 0

        logger.info("Basic counts:")
        logger.info(f"  Tracks: {track_count}")
        logger.info(f"  Artists: {artist_count}")
        logger.info(f"  Albums: {album_count}")
        logger.info(f"  Genres: {genre_count}")
        logger.info(f"  Moods: {mood_count}")

        result = {
            "total_tracks": track_count,
            "total_artists": artist_count,
            "total_albums": album_count,
            "total_genres": genre_count,
            "total_moods": mood_count,
        }

        logger.info("=== END DEBUG _get_basic_counts ===")
        return result

    def _get_leaderboards(self, session):
        """Get top performers in various categories with proper structure"""
        leaderboards = {}

        # Top artists by plays
        leaderboards["top_artists"] = self._get_top_artists(session)

        # Top genres by plays
        leaderboards["top_genres"] = self._get_top_genres(session)

        # Top moods by plays
        leaderboards["top_moods"] = self._get_top_moods(session)

        # Most played tracks
        leaderboards["top_tracks"] = self._get_top_tracks(session)

        # Get rating-based leaderboards and include them directly
        rating_leaderboards = self._get_rating_leaderboards(session)
        leaderboards.update(rating_leaderboards)

        return leaderboards

    def _get_top_artists(self, session, limit=10):
        """Get top artists by play count - return consistent format"""
        return (
            session.query(
                Artist,
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

    def _get_top_genres(self, session, limit=10):
        """Get top genres by play count - return consistent format"""
        return (
            session.query(
                Genre,
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

    def _get_top_moods(self, session, limit=10):
        """Get top moods by play count - return consistent format"""
        return (
            session.query(
                Mood,
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

    def _get_top_tracks(self, session, limit=10):
        """Get most played tracks"""
        return (
            session.query(Track, func.coalesce(Track.play_count, 0).label("play_count"))
            .order_by(func.coalesce(Track.play_count, 0).desc())
            .limit(limit)
            .all()
        )

    def _get_temporal_statistics(self, session):
        """Get time-based statistics - return proper structure for view"""
        # Create a subquery that gets the effective release year using proper joins
        tracks_with_years = (
            session.query(
                Track.track_id,
                case(
                    (Album.release_year.isnot(None), Album.release_year), else_=None
                ).label("effective_release_year"),
            )
            .join(Album, Track.album_id == Album.album_id)
            .subquery()
        )

        # Now use the subquery for decades calculation
        decades_case = case(
            (tracks_with_years.c.effective_release_year.between(1920, 1929), "1920s"),
            (tracks_with_years.c.effective_release_year.between(1930, 1939), "1930s"),
            (tracks_with_years.c.effective_release_year.between(1940, 1949), "1940s"),
            (tracks_with_years.c.effective_release_year.between(1950, 1959), "1950s"),
            (tracks_with_years.c.effective_release_year.between(1960, 1969), "1960s"),
            (tracks_with_years.c.effective_release_year.between(1970, 1979), "1970s"),
            (tracks_with_years.c.effective_release_year.between(1980, 1989), "1980s"),
            (tracks_with_years.c.effective_release_year.between(1990, 1999), "1990s"),
            (tracks_with_years.c.effective_release_year.between(2000, 2009), "2000s"),
            (tracks_with_years.c.effective_release_year.between(2010, 2019), "2010s"),
            (tracks_with_years.c.effective_release_year.between(2020, 2029), "2020s"),
            else_="Unknown",
        ).label("decade")

        # Decades breakdown using the subquery
        decades = (
            session.query(
                decades_case,
                func.count(tracks_with_years.c.track_id).label("track_count"),
            )
            .select_from(tracks_with_years)
            .filter(tracks_with_years.c.effective_release_year.isnot(None))
            .group_by(decades_case)
            .order_by(decades_case)
            .all()
        )

        # Release years breakdown - get top 20 years by track count
        release_years = (
            session.query(
                tracks_with_years.c.effective_release_year.label("release_year"),
                func.count(tracks_with_years.c.track_id).label("track_count"),
            )
            .filter(tracks_with_years.c.effective_release_year.isnot(None))
            .group_by(tracks_with_years.c.effective_release_year)
            .order_by(func.count(tracks_with_years.c.track_id).desc())
            .limit(20)
            .all()
        )

        # Recent activity
        recent_tracks = (
            session.query(Track)
            .filter(Track.date_added.isnot(None))
            .order_by(Track.date_added.desc())
            .limit(5)
            .all()
        )

        # Calculate tracks per year average using the subquery
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
            "recently_added": recent_tracks,
            "avg_tracks_per_year": round(avg_tracks_per_year, 1),
            "unique_years_count": years_with_tracks,
        }

    def _get_rating_leaderboards(self, session):
        """Get top performers by rating - return consistent format"""
        # Highest rated artists (with minimum play count)
        highest_rated_artists = (
            session.query(
                Artist,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Artist)
            .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .filter(Track.user_rating.isnot(None), Track.play_count > 5)
            .group_by(Artist.artist_id, Artist.artist_name)
            .having(func.count(Track.track_id) >= 3)  # Minimum 3 rated tracks
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Highest rated albums
        highest_rated_albums = (
            session.query(
                Album,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Album)
            .join(Track, Album.album_id == Track.album_id)
            .filter(Track.user_rating.isnot(None), Track.play_count > 0)
            .group_by(Album.album_id, Album.album_name)
            .having(func.count(Track.track_id) >= 3)  # Minimum 3 tracks
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Highest rated genres
        highest_rated_genres = (
            session.query(
                Genre,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .filter(Track.user_rating.isnot(None), Track.play_count > 0)
            .group_by(Genre.genre_id, Genre.genre_name)
            .having(func.count(Track.track_id) >= 5)  # Minimum 5 tracks
            .order_by(func.avg(Track.user_rating).desc())
            .limit(5)
            .all()
        )

        # Lowest rated genres
        lowest_rated_genres = (
            session.query(
                Genre,
                func.avg(Track.user_rating).label("avg_rating"),
            )
            .select_from(Genre)
            .join(TrackGenre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, TrackGenre.track_id == Track.track_id)
            .filter(Track.user_rating.isnot(None), Track.play_count > 0)
            .group_by(Genre.genre_id, Genre.genre_name)
            .having(func.count(Track.track_id) >= 5)  # Minimum 5 tracks
            .order_by(func.avg(Track.user_rating).asc())
            .limit(5)
            .all()
        )

        return {
            "highest_rated_artists": highest_rated_artists,
            "highest_rated_albums": highest_rated_albums,
            "highest_rated_genres": highest_rated_genres,
            "lowest_rated_genres": lowest_rated_genres,
        }

    def get_on_this_day(self):
        """Get music events for today's date"""
        session = self.session_factory()
        try:
            today = datetime.now()
            month, day = today.month, today.day

            results = {
                "albums_released": session.query(Album)
                .filter(Album.release_month == month, Album.release_day == day)
                .all(),
                "artists_born": session.query(Artist)
                .filter(Artist.begin_month == month, Artist.begin_day == day)
                .all(),
                "artists_died": session.query(Artist)
                .filter(Artist.end_month == month, Artist.end_day == day)
                .all(),
                "tracks_recorded": session.query(Track)
                .filter(Track.recorded_month == month, Track.recorded_day == day)
                .all(),
                "tracks_composed": session.query(Track)
                .filter(Track.composed_month == month, Track.composed_day == day)
                .all(),
            }

            return results
        finally:
            session.close()

    def get_metadata_completeness(self):
        """Calculate metadata completeness percentages"""
        session = self.session_factory()
        try:
            total_tracks = session.query(func.count(Track.track_id)).scalar()

            if total_tracks == 0:
                return {}

            # Fix the artwork query - use explicit join instead of association proxy
            tracks_with_artwork_count = (
                session.query(func.count(Track.track_id))
                .join(Album, Track.album_id == Album.album_id)
                .filter(Album.front_cover_path.isnot(None))
                .scalar()
            )

            completeness = {
                "tracks_with_rating": session.query(func.count(Track.track_id))
                .filter(Track.user_rating.isnot(None))
                .scalar()
                / total_tracks
                * 100,
                "tracks_with_lyrics": session.query(func.count(Track.track_id))
                .filter(Track.lyrics.isnot(None))
                .scalar()
                / total_tracks
                * 100,
                "tracks_with_artwork": tracks_with_artwork_count
                / total_tracks
                * 100,  # ✅ Fixed
                "tracks_with_audio_analysis": session.query(func.count(Track.track_id))
                .filter(Track.bpm.isnot(None))
                .scalar()
                / total_tracks
                * 100,
            }

            # Round percentages
            return {k: round(v, 1) for k, v in completeness.items()}
        finally:
            session.close()

    def get_ratings_distribution(self):
        """Get the distribution of tracks across rating values (0-10 scale)"""
        session = self.session_factory()
        try:
            # Get counts for each rating value (0.0 to 10.0 in 0.5 increments)
            ratings_distribution = (
                session.query(
                    Track.user_rating, func.count(Track.track_id).label("track_count")
                )
                .filter(Track.user_rating.isnot(None))
                .group_by(Track.user_rating)
                .order_by(Track.user_rating)
                .all()
            )

            # Convert to dictionary with proper float keys
            distribution_dict = {}
            for rating, count in ratings_distribution:
                if rating is not None:
                    distribution_dict[float(rating)] = count

            # Get summary statistics
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

            # Calculate average rating
            avg_rating_result = (
                session.query(func.avg(Track.user_rating))
                .filter(Track.user_rating.isnot(None))
                .scalar()
            )
            avg_rating = float(avg_rating_result) if avg_rating_result else 0.0

            # Find most common rating
            most_common_rating = None
            if distribution_dict:
                most_common_rating = max(distribution_dict.items(), key=lambda x: x[1])[
                    0
                ]

            return {
                "distribution": distribution_dict,
                "summary": {
                    "total_rated_tracks": total_rated_tracks,
                    "unrated_tracks": unrated_tracks,
                    "total_tracks": total_tracks,
                    "rating_completeness": round(
                        (total_rated_tracks / total_tracks * 100)
                        if total_tracks > 0
                        else 0,
                        1,
                    ),
                    "average_rating": round(avg_rating, 2),
                    "most_common_rating": most_common_rating,
                },
            }
        finally:
            session.close()

    def _get_audio_quality_stats(self, session):
        """Get audio quality metrics with debugging"""
        logger.info("=== DEBUG _get_audio_quality_stats ===")

        # Check sample data
        sample_tracks = (
            session.query(
                Track.track_id,
                Track.track_name,
                Track.bit_rate,
                Track.bit_depth,
                Track.file_size,
                Track.duration,
            )
            .limit(5)
            .all()
        )

        logger.info("Sample audio quality data:")
        for track in sample_tracks:
            logger.info(
                f"  Track: {track.track_name}, Bit Rate: {track.bit_rate}, "
                f"Bit Depth: {track.bit_depth}, File Size: {track.file_size}, "
                f"Duration: {track.duration}"
            )

        # Check data availability
        total_tracks = session.query(func.count(Track.track_id)).scalar()
        valid_bit_rate_count = (
            session.query(func.count(Track.track_id))
            .filter(Track.bit_rate.isnot(None))
            .scalar()
        )
        valid_bit_depth_count = (
            session.query(func.count(Track.track_id))
            .filter(Track.bit_depth.isnot(None))
            .scalar()
        )
        valid_filesize_count = (
            session.query(func.count(Track.track_id))
            .filter(Track.file_size.isnot(None))
            .scalar()
        )
        valid_duration_count = (
            session.query(func.count(Track.track_id))
            .filter(Track.duration.isnot(None))
            .scalar()
        )

        logger.info("Data availability:")
        logger.info(f"  Total tracks: {total_tracks}")
        logger.info(f"  Tracks with bit rate: {valid_bit_rate_count}")
        logger.info(f"  Tracks with bit depth: {valid_bit_depth_count}")
        logger.info(f"  Tracks with file size: {valid_filesize_count}")
        logger.info(f"  Tracks with duration: {valid_duration_count}")

        # Execute main query
        quality_stats = session.query(
            func.avg(Track.bit_rate).label("avg_bit_rate"),
            func.avg(Track.bit_depth).label("avg_bit_depth"),
            func.avg(Track.file_size).label("avg_file_size"),
            func.avg(Track.duration).label("avg_duration"),
        ).one()

        logger.info("Raw audio quality query results:")
        logger.info(f"  Avg bit rate: {quality_stats.avg_bit_rate}")
        logger.info(f"  Avg bit depth: {quality_stats.avg_bit_depth}")
        logger.info(f"  Avg file size: {quality_stats.avg_file_size}")
        logger.info(f"  Avg duration: {quality_stats.avg_duration}")

        result = {
            "average_bit_rate": round(quality_stats.avg_bit_rate or 0),
            "average_bit_depth": round(quality_stats.avg_bit_depth or 0, 1),
            "average_file_size": round(quality_stats.avg_file_size or 0),
            "average_duration": round(quality_stats.avg_duration or 0),
            "tracks_with_bit_rate": valid_bit_rate_count,
            "tracks_with_bit_depth": valid_bit_depth_count,
        }

        logger.info("Final audio quality statistics result:")
        for key, value in result.items():
            logger.info(f"  {key}: {value}")

        logger.info("=== END DEBUG _get_audio_quality_stats ===")
        return result

    def get_file_format_distribution(self):
        """Get distribution of tracks by file extension"""
        session = self.session_factory()
        try:
            # Query to get file extension counts using the dedicated file_extension field
            format_distribution = (
                session.query(
                    Track.file_extension,
                    func.count(Track.track_id).label("track_count"),
                )
                .filter(Track.file_extension.isnot(None))
                .group_by(Track.file_extension)
                .order_by(func.count(Track.track_id).desc())
                .all()
            )

            # Convert to dictionary with cleaned format names
            distribution_dict = {}
            for extension, count in format_distribution:
                if extension:
                    # Clean up the extension (remove dots, convert to uppercase)
                    clean_extension = str(extension).lstrip(".").upper()
                    distribution_dict[clean_extension] = count

            logger.info(
                f"File format distribution found: {len(distribution_dict)} formats"
            )
            return distribution_dict

        except Exception as e:
            logger.error(f"Error getting file format distribution: {e}")
            return {}
        finally:
            session.close()
