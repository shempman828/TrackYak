"""
stats/places_credits.py

PlacesCreditsStats: power-of-10 rating leaderboards for places and
publishers, recursive country ratings and highest-rated-artist-by-country
(via Place.recursive_rated_tracks / recursive_artist_ids), composer
prolific/rating stats, role-credit-count stats (most credits vs. most
distinct roles), rating comparison across the top 25% of roles by credit
count, and per-role prolific/top-rated artist leaderboards.
"""

import math

from sqlalchemy import func

from src.db.db_tables import (
    Album,
    AlbumPublisher,
    AlbumRoleAssociation,
    Artist,
    Place,
    Publisher,
    Role,
    Track,
    TrackArtistRole,
)
from src.statistics.stats.helpers import (
    RATING_MAX,
    RATING_MIN,
    distinct_artist_track_subquery,
    threshold_leaderboard,
)

# Publishers rarely reach the 10/100/1000 *rated-track* scale used
# elsewhere -- their natural unit is albums released, which is much
# smaller, so these tiers are sized for album counts instead.
PUBLISHER_ALBUM_THRESHOLDS = (5, 20, 100)

# Minimum rated tracks for a country's recursive rollup, or an artist's own
# catalog, to be considered for a "highest/lowest rated" comparison.
COUNTRY_RATING_MIN_N = 10
ARTIST_BY_COUNTRY_MIN_N = 3

# Fraction of roles (by track-credit count) included in the role rating
# comparison -- keeps one-off/rare roles from cluttering the comparison.
ROLE_RATING_TOP_FRACTION = 0.25


class PlacesCreditsStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_places_credits_stats(self):
        session = self.session_factory()
        try:
            return {
                "rated_places_leaderboard": self._rated_places_leaderboard(session),
                "rated_countries": self._rated_countries(session),
                "highest_rated_artist_by_country": self._highest_rated_artist_by_country(
                    session
                ),
                "rated_publishers_leaderboard": self._rated_publishers_leaderboard(
                    session
                ),
                "most_prolific_composer": self._most_prolific_composer(session),
                "rated_composers_leaderboard": self._rated_composers_leaderboard(
                    session
                ),
                "role_credit_counts": self._role_credit_counts(session),
                "role_rating_comparison": self._role_rating_comparison(session),
                "prolific_artist_by_role": self._prolific_artist_by_role(session),
                "top_rated_artist_by_role": self._top_rated_artist_by_role(session),
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Places (direct association, power-of-10)                           #
    # ------------------------------------------------------------------ #

    def _rated_places_leaderboard(self, session):
        base_query = (
            session.query(Place)
            .join(Place.tracks)
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
                Place.place_id,
                Place.place_name,
                Track.user_rating,
                ascending=False,
            ),
            "lowest": threshold_leaderboard(
                session,
                base_query,
                Place.place_id,
                Place.place_name,
                Track.user_rating,
                ascending=True,
            ),
        }

    # ------------------------------------------------------------------ #
    #  Countries (recursive rollup)                                       #
    # ------------------------------------------------------------------ #

    def _rated_countries(self, session, min_n=COUNTRY_RATING_MIN_N):
        countries = session.query(Place).filter(Place.place_type == "Country").all()
        results = []
        for country in countries:
            ratings = [r for _tid, r in country.recursive_rated_tracks]
            if len(ratings) >= min_n:
                avg = sum(ratings) / len(ratings)
                results.append((country.place_name, round(avg, 2), len(ratings)))

        results.sort(key=lambda r: r[1], reverse=True)
        return {
            "highest": results[:5],
            "lowest": results[-5:][::-1] if results else [],
        }

    def _highest_rated_artist_by_country(
        self, session, min_rated_tracks=ARTIST_BY_COUNTRY_MIN_N
    ):
        dedup = distinct_artist_track_subquery(session)
        artist_ratings = dict(
            (artist_id, (round(avg_rating, 2), n))
            for artist_id, avg_rating, n in session.query(
                Artist.artist_id,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .join(dedup, Artist.artist_id == dedup.c.artist_id)
            .join(Track, dedup.c.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Artist.artist_id)
            .having(func.count(Track.track_id) >= min_rated_tracks)
            .all()
        )
        if not artist_ratings:
            return {}

        artist_names = dict(session.query(Artist.artist_id, Artist.artist_name).all())

        countries = session.query(Place).filter(Place.place_type == "Country").all()
        results = {}
        for country in countries:
            best = None
            for artist_id in country.recursive_artist_ids:
                rating = artist_ratings.get(artist_id)
                if rating and (best is None or rating[0] > best[1]):
                    best = (artist_id, rating[0])
            if best:
                artist_id, avg_rating = best
                results[country.place_name] = (
                    artist_names.get(artist_id, f"Artist {artist_id}"),
                    avg_rating,
                )
        return results

    # ------------------------------------------------------------------ #
    #  Publishers (power-of-10 album counts)                              #
    # ------------------------------------------------------------------ #

    def _rated_publishers_leaderboard(self, session):
        order_expr = func.avg(Track.user_rating)
        album_count_expr = func.count(func.distinct(Album.album_id))

        def _leaderboard(ascending, limit=5):
            order = order_expr.asc() if ascending else order_expr.desc()
            results = {}
            for threshold in PUBLISHER_ALBUM_THRESHOLDS:
                rows = (
                    session.query(
                        Publisher.publisher_name,
                        order_expr.label("avg_rating"),
                        album_count_expr.label("album_count"),
                    )
                    .select_from(Publisher)
                    .join(AlbumPublisher, Publisher.publisher_id == AlbumPublisher.publisher_id)
                    .join(Album, AlbumPublisher.album_id == Album.album_id)
                    .join(Track, Album.album_id == Track.album_id)
                    .filter(
                        Track.user_rating.isnot(None),
                        Track.user_rating >= RATING_MIN,
                        Track.user_rating <= RATING_MAX,
                    )
                    .group_by(Publisher.publisher_id, Publisher.publisher_name)
                    .having(album_count_expr >= threshold)
                    .order_by(order)
                    .limit(limit)
                    .all()
                )
                results[threshold] = [
                    (name, round(avg_rating, 2), album_count)
                    for name, avg_rating, album_count in rows
                ]
            return results

        return {"highest": _leaderboard(False), "lowest": _leaderboard(True)}

    # ------------------------------------------------------------------ #
    #  Composers                                                           #
    # ------------------------------------------------------------------ #

    def _composer_base_query(self, session):
        return (
            session.query(Artist)
            .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
            .join(Role, TrackArtistRole.role_id == Role.role_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .filter(Role.role_name == "Composer")
        )

    def _most_prolific_composer(self, session, limit=5):
        rows = (
            session.query(Artist.artist_name, func.count(Track.track_id))
            .select_from(Artist)
            .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
            .join(Role, TrackArtistRole.role_id == Role.role_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .filter(Role.role_name == "Composer")
            .group_by(Artist.artist_id, Artist.artist_name)
            .order_by(func.count(Track.track_id).desc())
            .limit(limit)
            .all()
        )
        return [(name, count) for name, count in rows]

    def _rated_composers_leaderboard(self, session):
        base_query = self._composer_base_query(session).filter(
            Track.user_rating.isnot(None),
            Track.user_rating >= RATING_MIN,
            Track.user_rating <= RATING_MAX,
        )
        return {
            "highest": threshold_leaderboard(
                session,
                base_query,
                Artist.artist_id,
                Artist.artist_name,
                Track.user_rating,
                ascending=False,
            ),
            "lowest": threshold_leaderboard(
                session,
                base_query,
                Artist.artist_id,
                Artist.artist_name,
                Track.user_rating,
                ascending=True,
            ),
        }

    # ------------------------------------------------------------------ #
    #  Role-credit-count stats                                            #
    # ------------------------------------------------------------------ #

    def _credit_union_rows(self, session):
        """(artist_id, role_id) pairs across both track- and album-level
        credits, unioned so an artist's role footprint counts both. Both
        sides label their columns identically since an ORM-attribute query
        (rather than a Core select) otherwise auto-names each side's
        columns after its own table, leaving the union's column names
        mismatched."""
        track_credits = session.query(
            TrackArtistRole.artist_id.label("artist_id"),
            TrackArtistRole.role_id.label("role_id"),
        )
        album_credits = session.query(
            AlbumRoleAssociation.artist_id.label("artist_id"),
            AlbumRoleAssociation.role_id.label("role_id"),
        )
        return track_credits.union_all(album_credits).subquery()

    def _role_credit_counts(self, session, limit=5):
        credits = self._credit_union_rows(session)
        rows = (
            session.query(
                Artist.artist_name,
                func.count().label("total_credits"),
                func.count(func.distinct(credits.c.role_id)).label("distinct_roles"),
            )
            .select_from(credits)
            .join(Artist, Artist.artist_id == credits.c.artist_id)
            .group_by(Artist.artist_id, Artist.artist_name)
            .all()
        )

        by_total = sorted(rows, key=lambda r: r[1], reverse=True)[:limit]
        by_distinct = sorted(rows, key=lambda r: r[2], reverse=True)[:limit]
        return {
            "most_credits": [(name, total) for name, total, _distinct in by_total],
            "most_distinct_roles": [
                (name, distinct) for name, _total, distinct in by_distinct
            ],
        }

    def _role_rating_comparison(self, session):
        role_counts = (
            session.query(Role.role_id, Role.role_name, func.count(TrackArtistRole.track_id))
            .join(TrackArtistRole, Role.role_id == TrackArtistRole.role_id)
            .group_by(Role.role_id, Role.role_name)
            .order_by(func.count(TrackArtistRole.track_id).desc())
            .all()
        )
        if not role_counts:
            return []

        top_n = max(1, math.ceil(len(role_counts) * ROLE_RATING_TOP_FRACTION))
        top_role_ids = [role_id for role_id, _name, _count in role_counts[:top_n]]

        rows = (
            session.query(
                Role.role_name,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .select_from(Role)
            .join(TrackArtistRole, Role.role_id == TrackArtistRole.role_id)
            .join(Track, TrackArtistRole.track_id == Track.track_id)
            .filter(
                Role.role_id.in_(top_role_ids),
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Role.role_id, Role.role_name)
            .all()
        )
        ratings = [(name, round(avg, 2), n) for name, avg, n in rows]
        ratings.sort(key=lambda r: r[1], reverse=True)
        return ratings

    # ------------------------------------------------------------------ #
    #  Per-role prolific / top-rated artist leaderboards                  #
    # ------------------------------------------------------------------ #

    def _prolific_artist_by_role(self, session, limit=5):
        roles = session.query(Role.role_id, Role.role_name).join(
            TrackArtistRole, Role.role_id == TrackArtistRole.role_id
        ).distinct().all()

        results = {}
        for role_id, role_name in roles:
            rows = (
                session.query(Artist.artist_name, func.count(Track.track_id))
                .select_from(Artist)
                .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
                .join(Track, TrackArtistRole.track_id == Track.track_id)
                .filter(TrackArtistRole.role_id == role_id)
                .group_by(Artist.artist_id, Artist.artist_name)
                .order_by(func.count(Track.track_id).desc())
                .limit(limit)
                .all()
            )
            results[role_name] = [(name, count) for name, count in rows]
        return results

    def _top_rated_artist_by_role(self, session):
        roles = session.query(Role.role_id, Role.role_name).join(
            TrackArtistRole, Role.role_id == TrackArtistRole.role_id
        ).distinct().all()

        results = {}
        for role_id, role_name in roles:
            base_query = (
                session.query(Artist)
                .join(TrackArtistRole, Artist.artist_id == TrackArtistRole.artist_id)
                .join(Track, TrackArtistRole.track_id == Track.track_id)
                .filter(
                    TrackArtistRole.role_id == role_id,
                    Track.user_rating.isnot(None),
                    Track.user_rating >= RATING_MIN,
                    Track.user_rating <= RATING_MAX,
                )
            )
            results[role_name] = {
                "highest": threshold_leaderboard(
                    session,
                    base_query,
                    Artist.artist_id,
                    Artist.artist_name,
                    Track.user_rating,
                    ascending=False,
                ),
                "lowest": threshold_leaderboard(
                    session,
                    base_query,
                    Artist.artist_id,
                    Artist.artist_name,
                    Track.user_rating,
                    ascending=True,
                ),
            }
        return results
