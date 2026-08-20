"""
stats/artists.py

ArtistStats: average artist rating by generation, artist type/religion/
gender distribution and rating comparisons, highest/lowest rated artist
disambiguated by gender (power-of-10), and lifespan stats (oldest living,
longest/shortest lived, youngest). Role-credit-count stats, rating-by-role,
per-role leaderboards, and highest-rated artist by country live in
stats/places_credits.py instead, since they're credit/place joins rather
than plain artist-table queries.
"""

from sqlalchemy import func, or_

from src.db.db_tables import (
    Artist,
    ArtistType,
    ArtistTypeAssociation,
    Religion,
    Track,
)
from src.statistics.stats.helpers import (
    RATING_MAX,
    RATING_MIN,
    distinct_artist_track_subquery,
    threshold_leaderboard,
)

# Standard Pew-style generation boundaries (inclusive), keyed by begin_year.
# Artists whose begin_year falls outside every range are excluded from the
# generation-ratings breakdown rather than bucketed into a catch-all.
GENERATIONS = (
    ("Boomer", 1946, 1964),
    ("Gen X", 1965, 1980),
    ("Millennial", 1981, 1996),
    ("Gen Z", 1997, 2012),
)

# Minimum rated tracks for a bucket (generation/type/religion/gender) to be
# included in a rating comparison -- keeps small buckets from producing a
# noisy "highest rated" result off a handful of tracks.
RATING_BUCKET_MIN_N = 10

# begin_year/end_year double as founded/split for groups -- lifespan stats
# only make sense for people, so include isgroup == 0 or unset, exclude
# isgroup == 1. `is_(None)` is needed alongside `!= 1` since SQL NULL != 1
# evaluates to NULL (excluded), not True.
PERSON_FILTER = or_(Artist.isgroup.is_(None), Artist.isgroup != 1)


def _normalized_gender(session_column):
    return func.lower(func.trim(session_column))


class ArtistStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_artist_stats(self):
        session = self.session_factory()
        try:
            return {
                "generation_ratings": self._generation_ratings(session),
                "artist_type_distribution": self._artist_type_distribution(session),
                "artist_type_rating": self._artist_type_rating(session),
                "highest_rated_artist_per_type": self._highest_rated_artist_per_type(
                    session
                ),
                "artist_religion_distribution": self._artist_religion_distribution(
                    session
                ),
                "religion_rating_comparison": self._religion_rating_comparison(
                    session
                ),
                "gender_rating_comparison": self._gender_rating_comparison(session),
                "rated_artists_by_gender": self._rated_artists_by_gender(session),
                "lifespan_stats": self._lifespan_stats(session),
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Generation ratings                                                  #
    # ------------------------------------------------------------------ #

    def _generation_ratings(self, session):
        dedup = distinct_artist_track_subquery(session)
        results = []
        for label, start_year, end_year in GENERATIONS:
            row = (
                session.query(
                    func.avg(Track.user_rating).label("avg_rating"),
                    func.count(Track.track_id).label("n"),
                )
                .select_from(Artist)
                .join(dedup, Artist.artist_id == dedup.c.artist_id)
                .join(Track, dedup.c.track_id == Track.track_id)
                .filter(
                    Artist.begin_year >= start_year,
                    Artist.begin_year <= end_year,
                    Track.user_rating.isnot(None),
                    Track.user_rating >= RATING_MIN,
                    Track.user_rating <= RATING_MAX,
                )
                .one()
            )
            if row.n and row.n >= RATING_BUCKET_MIN_N:
                results.append((label, round(row.avg_rating, 2), row.n))
        return results

    # ------------------------------------------------------------------ #
    #  Artist type distribution / rating                                   #
    # ------------------------------------------------------------------ #

    def _artist_type_distribution(self, session):
        rows = (
            session.query(ArtistType.type_name, func.count(ArtistTypeAssociation.artist_id))
            .join(
                ArtistTypeAssociation,
                ArtistType.artist_type_id == ArtistTypeAssociation.artist_type_id,
            )
            .group_by(ArtistType.artist_type_id, ArtistType.type_name)
            .order_by(func.count(ArtistTypeAssociation.artist_id).desc())
            .all()
        )
        return {name: count for name, count in rows}

    def _artist_type_rating(self, session):
        dedup = distinct_artist_track_subquery(session)
        rows = (
            session.query(
                ArtistType.type_name,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .select_from(ArtistType)
            .join(
                ArtistTypeAssociation,
                ArtistType.artist_type_id == ArtistTypeAssociation.artist_type_id,
            )
            .join(dedup, ArtistTypeAssociation.artist_id == dedup.c.artist_id)
            .join(Track, dedup.c.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(ArtistType.artist_type_id, ArtistType.type_name)
            .having(func.count(Track.track_id) >= RATING_BUCKET_MIN_N)
            .all()
        )
        ratings = [(name, round(avg, 2), n) for name, avg, n in rows]
        ratings.sort(key=lambda r: r[1], reverse=True)
        return {
            "highest": ratings[:5],
            "lowest": ratings[-5:][::-1] if ratings else [],
        }

    def _highest_rated_artist_per_type(self, session, min_rated_tracks=3):
        dedup = distinct_artist_track_subquery(session)
        rows = (
            session.query(
                ArtistType.type_name,
                Artist.artist_id,
                Artist.artist_name,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .select_from(ArtistType)
            .join(
                ArtistTypeAssociation,
                ArtistType.artist_type_id == ArtistTypeAssociation.artist_type_id,
            )
            .join(Artist, ArtistTypeAssociation.artist_id == Artist.artist_id)
            .join(dedup, Artist.artist_id == dedup.c.artist_id)
            .join(Track, dedup.c.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(ArtistType.artist_type_id, ArtistType.type_name, Artist.artist_id, Artist.artist_name)
            .having(func.count(Track.track_id) >= min_rated_tracks)
            .all()
        )

        best_by_type = {}
        for type_name, _artist_id, artist_name, avg_rating, _n in rows:
            current = best_by_type.get(type_name)
            if current is None or avg_rating > current[1]:
                best_by_type[type_name] = (artist_name, round(avg_rating, 2))
        return best_by_type

    # ------------------------------------------------------------------ #
    #  Religion distribution / rating                                      #
    # ------------------------------------------------------------------ #

    def _artist_religion_distribution(self, session):
        rows = (
            session.query(Religion.religion_name, func.count(Artist.artist_id))
            .join(Artist, Religion.religion_id == Artist.religion_id)
            .group_by(Religion.religion_id, Religion.religion_name)
            .order_by(func.count(Artist.artist_id).desc())
            .all()
        )
        return {name: count for name, count in rows}

    def _religion_rating_comparison(self, session):
        dedup = distinct_artist_track_subquery(session)
        rows = (
            session.query(
                Religion.religion_name,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .select_from(Religion)
            .join(Artist, Religion.religion_id == Artist.religion_id)
            .join(dedup, Artist.artist_id == dedup.c.artist_id)
            .join(Track, dedup.c.track_id == Track.track_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Religion.religion_id, Religion.religion_name)
            .having(func.count(Track.track_id) >= RATING_BUCKET_MIN_N)
            .all()
        )
        ratings = [(name, round(avg, 2), n) for name, avg, n in rows]
        ratings.sort(key=lambda r: r[1], reverse=True)
        return ratings

    # ------------------------------------------------------------------ #
    #  Gender rating comparison / leaderboard                              #
    # ------------------------------------------------------------------ #

    def _gender_rating_comparison(self, session):
        gender_expr = _normalized_gender(Artist.gender)
        dedup = distinct_artist_track_subquery(session)
        rows = (
            session.query(
                gender_expr.label("gender"),
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .select_from(Artist)
            .join(dedup, Artist.artist_id == dedup.c.artist_id)
            .join(Track, dedup.c.track_id == Track.track_id)
            .filter(
                Artist.gender.isnot(None),
                Artist.gender != "",
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by("gender")
            .having(func.count(Track.track_id) >= RATING_BUCKET_MIN_N)
            .all()
        )
        return [(gender, round(avg, 2), n) for gender, avg, n in rows]

    def _rated_artists_by_gender(self, session):
        gender_expr = _normalized_gender(Artist.gender)
        dedup = distinct_artist_track_subquery(session)
        genders = [
            g
            for (g,) in session.query(gender_expr)
            .filter(Artist.gender.isnot(None), Artist.gender != "")
            .distinct()
            .all()
        ]

        results = {}
        for gender in genders:
            base_query = (
                session.query(Artist)
                .join(dedup, Artist.artist_id == dedup.c.artist_id)
                .join(Track, dedup.c.track_id == Track.track_id)
                .filter(
                    gender_expr == gender,
                    Track.user_rating.isnot(None),
                    Track.user_rating >= RATING_MIN,
                    Track.user_rating <= RATING_MAX,
                )
            )
            results[gender] = {
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

    # ------------------------------------------------------------------ #
    #  Lifespan stats                                                      #
    # ------------------------------------------------------------------ #

    def _lifespan_stats(self, session):
        oldest_living = (
            session.query(Artist.artist_name, Artist.begin_year)
            .filter(
                PERSON_FILTER, Artist.end_year.is_(None), Artist.begin_year.isnot(None)
            )
            .order_by(Artist.begin_year.asc())
            .first()
        )
        youngest = (
            session.query(Artist.artist_name, Artist.begin_year)
            .filter(PERSON_FILTER, Artist.begin_year.isnot(None))
            .order_by(Artist.begin_year.desc())
            .first()
        )

        lifespan_expr = Artist.end_year - Artist.begin_year
        lifespan_filter = (
            PERSON_FILTER,
            Artist.begin_year.isnot(None),
            Artist.end_year.isnot(None),
            lifespan_expr >= 0,
        )
        longest_lived = (
            session.query(
                Artist.artist_name, Artist.begin_year, Artist.end_year, lifespan_expr
            )
            .filter(*lifespan_filter)
            .order_by(lifespan_expr.desc())
            .first()
        )
        shortest_lived = (
            session.query(
                Artist.artist_name, Artist.begin_year, Artist.end_year, lifespan_expr
            )
            .filter(*lifespan_filter)
            .order_by(lifespan_expr.asc())
            .first()
        )

        return {
            "oldest_living": (
                {"name": oldest_living[0], "begin_year": oldest_living[1]}
                if oldest_living
                else None
            ),
            "youngest": (
                {"name": youngest[0], "begin_year": youngest[1]} if youngest else None
            ),
            "longest_lived": (
                {
                    "name": longest_lived[0],
                    "begin_year": longest_lived[1],
                    "end_year": longest_lived[2],
                    "years": longest_lived[3],
                }
                if longest_lived
                else None
            ),
            "shortest_lived": (
                {
                    "name": shortest_lived[0],
                    "begin_year": shortest_lived[1],
                    "end_year": shortest_lived[2],
                    "years": shortest_lived[3],
                }
                if shortest_lived
                else None
            ),
        }
