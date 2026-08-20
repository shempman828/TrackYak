"""
stats/albums.py

AlbumStats: rating controlled by track count, sales distribution + top/
bottom 5, release-country distribution + highest rated album per country
(flat, not recursive -- Album.release_country is a free-text string, not
linked into the Place hierarchy), and most diverse album by genre spread.

Genre-spread diversity formula (locked decision from the planning phase):
for each album, group its distinct genres by top-level root genre, weight
each branch 1 + log(1 + genres-in-branch), and sum the weights across
branches. An album whose genres cluster under one root scores low even with
many genres; one that spans several unrelated root genres scores higher.
"""

import math

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.db.db_tables import Album, Genre, Track
from src.statistics.stats.helpers import (
    RATING_MAX,
    RATING_MIN,
    distribution_stats,
    sparse_data_guard,
    threshold_leaderboard,
)

# Albums rarely run into the hundreds of tracks, so the genre/publisher-style
# 10/100/1000 tiers don't apply -- these are sized for a typical tracklist.
ALBUM_RATING_THRESHOLDS = (3, 5, 10)


def _genre_root_names(session):
    """genre_id -> top-level root genre_name, computed from one flat query
    over (genre_id, genre_name, parent_id) rather than walking
    Genre.parent per genre -- that lazy-loads one query per level per
    genre, which adds up once this runs per-album across the library."""
    rows = session.query(Genre.genre_id, Genre.genre_name, Genre.parent_id).all()
    by_id = {genre_id: (name, parent_id) for genre_id, name, parent_id in rows}

    roots = {}

    def root_of(genre_id):
        if genre_id not in roots:
            name, parent_id = by_id[genre_id]
            roots[genre_id] = name if parent_id is None else root_of(parent_id)
        return roots[genre_id]

    return {genre_id: root_of(genre_id) for genre_id in by_id}


class AlbumStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_album_stats(self):
        session = self.session_factory()
        try:
            return {
                "rating_by_track_count": self._rating_by_track_count(session),
                "sales_distribution": self._sales_distribution(session),
                "top_bottom_selling_albums": self._top_bottom_selling_albums(session),
                "release_country_distribution": self._release_country_distribution(
                    session
                ),
                "highest_rated_album_by_country": self._highest_rated_album_by_country(
                    session
                ),
                "most_diverse_albums_by_genre": self._most_diverse_albums_by_genre(
                    session
                ),
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Highest rated album, controlled by track count                     #
    # ------------------------------------------------------------------ #

    def _rating_by_track_count(self, session):
        base_query = (
            session.query(Album)
            .join(Track, Album.album_id == Track.album_id)
            .filter(
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
        )
        return threshold_leaderboard(
            session,
            base_query,
            Album.album_id,
            Album.album_name,
            Track.user_rating,
            thresholds=ALBUM_RATING_THRESHOLDS,
            ascending=False,
        )

    # ------------------------------------------------------------------ #
    #  Sales                                                               #
    # ------------------------------------------------------------------ #

    def _sales_distribution(self, session):
        sales_query = session.query(Album).filter(Album.estimated_sales.isnot(None))
        if not sparse_data_guard(sales_query):
            return None
        values = [v for (v,) in session.query(Album.estimated_sales).filter(
            Album.estimated_sales.isnot(None)
        ).all()]
        return distribution_stats(values)

    def _top_bottom_selling_albums(self, session, n=5):
        sales_query = session.query(Album).filter(Album.estimated_sales.isnot(None))
        if not sparse_data_guard(sales_query):
            return {"top": [], "bottom": []}

        def _rows(ordered_query):
            return [
                (album.album_name, album.album_artist_names, album.estimated_sales)
                for album in ordered_query.limit(n).all()
            ]

        query = session.query(Album).filter(Album.estimated_sales.isnot(None))
        top = _rows(query.order_by(Album.estimated_sales.desc()))
        bottom = _rows(query.order_by(Album.estimated_sales.asc()))
        return {"top": top, "bottom": bottom}

    # ------------------------------------------------------------------ #
    #  Release country                                                     #
    # ------------------------------------------------------------------ #

    def _release_country_distribution(self, session):
        rows = (
            session.query(Album.release_country, func.count(Album.album_id))
            .filter(Album.release_country.isnot(None))
            .group_by(Album.release_country)
            .order_by(func.count(Album.album_id).desc())
            .all()
        )
        return {country: count for country, count in rows}

    def _highest_rated_album_by_country(self, session, min_rated_tracks=3):
        """For each release country, the single highest-rated album with at
        least `min_rated_tracks` rated tracks."""
        rows = (
            session.query(
                Album.release_country,
                Album.album_id,
                Album.album_name,
                func.avg(Track.user_rating).label("avg_rating"),
                func.count(Track.track_id).label("n"),
            )
            .join(Track, Album.album_id == Track.album_id)
            .filter(
                Album.release_country.isnot(None),
                Track.user_rating.isnot(None),
                Track.user_rating >= RATING_MIN,
                Track.user_rating <= RATING_MAX,
            )
            .group_by(Album.release_country, Album.album_id, Album.album_name)
            .having(func.count(Track.track_id) >= min_rated_tracks)
            .all()
        )

        best_by_country = {}
        for country, _album_id, album_name, avg_rating, _n in rows:
            current = best_by_country.get(country)
            if current is None or avg_rating > current[1]:
                best_by_country[country] = (album_name, round(avg_rating, 2))
        return best_by_country

    # ------------------------------------------------------------------ #
    #  Genre-spread diversity                                              #
    # ------------------------------------------------------------------ #

    def _most_diverse_albums_by_genre(self, session, limit=5):
        genre_roots = _genre_root_names(session)
        albums = (
            session.query(Album)
            .join(Track, Album.album_id == Track.album_id)
            .distinct()
            .options(selectinload(Album.tracks).selectinload(Track.genres))
            .all()
        )
        scored = []
        for album in albums:
            genre_ids = {genre.genre_id for track in album.tracks for genre in track.genres}
            if not genre_ids:
                continue
            branches = {}
            for genre_id in genre_ids:
                root = genre_roots.get(genre_id, "Unknown")
                branches[root] = branches.get(root, 0) + 1
            score = sum(1 + math.log(1 + count) for count in branches.values())
            scored.append((album.album_name, round(score, 3), len(branches)))

        scored.sort(key=lambda r: r[1], reverse=True)
        return scored[:limit]
