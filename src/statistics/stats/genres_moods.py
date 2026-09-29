"""
stats/genres_moods.py

GenreMoodStats: power-of-10 rating leaderboards for genres, most niche
genre (deepest nested chain with tracks), top/bottom 5 genres by track
count, the per-genre album release timeline (year distribution, earliest /
latest album, longest / shortest lasting genres), outlier-controlled mood ratings, most/least played mood, and the
5 most representative tracks per auto-tagged mood (by lyrics-match score).
"""

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.db.db_tables import Album, AlbumRoleAssociation, Genre, Mood, MoodTrackAssociation, Track, TrackGenre
from src.statistics.stats.helpers import RATING_MAX, RATING_MIN, outlier_controlled_average, threshold_leaderboard
from src.statistics.stats.lyrics import _tokenize

# A track needs at least this many lyric tokens to be eligible for a mood's
# "most representative" list. Without it, a short, near-all-keyword lyric
# ("happy happy happy") posts a runaway density and buries full songs.
REPRESENTATIVE_MIN_TOKENS = 20

# A genre needs at least this many dated albums to appear in the genre
# timeline's span list. It is the lowest tier the tab offers; the tab
# narrows further with rank_genre_spans().
GENRE_SPAN_MIN_ALBUMS = 3


def rank_genre_spans(spans, min_albums, longest, limit=5):
    """Filter `spans` rows (genre_name, span, first_year, last_year,
    n_albums) to genres with at least `min_albums` albums and return the
    `limit` longest (or shortest) lasting ones. Ties go to the genre with
    more albums, then to the genre name."""
    rows = [row for row in spans if row[4] >= min_albums]
    rows.sort(key=lambda r: (-r[1] if longest else r[1], -r[4], r[0]))
    return rows[:limit]


def _format_release_date(year, month, day):
    if month is None:
        return str(year)
    if day is None:
        return f"{year}-{month:02d}"
    return f"{year}-{month:02d}-{day:02d}"


def _earliest_key(album):
    # Unknown month/day sort after known ones in the same year.
    return (album.release_year, album.release_month is None, album.release_month or 0, album.release_day is None, album.release_day or 0, album.album_name or "")


def _latest_key(album):
    # Sorted ascending, so the first row is the latest; unknown month/day
    # still sort after known ones in the same year.
    return (-album.release_year, album.release_month is None, -(album.release_month or 0), album.release_day is None, -(album.release_day or 0), album.album_name or "")


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
                "genre_timeline": self._genre_timeline(session),
                "mood_ratings_outlier_controlled": self._mood_ratings(session),
                "mood_play_counts": self._mood_play_counts(session),
                "representative_tracks_per_mood": (self._representative_tracks_per_mood(session)),
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
            .filter(Track.user_rating.isnot(None), Track.user_rating >= RATING_MIN, Track.user_rating <= RATING_MAX)
        )
        return {
            "highest": threshold_leaderboard(session, base_query, Genre.genre_id, Genre.genre_name, Track.user_rating, ascending=False),
            "lowest": threshold_leaderboard(session, base_query, Genre.genre_id, Genre.genre_name, Track.user_rating, ascending=True),
        }

    # ------------------------------------------------------------------ #
    #  Most niche genre (deepest nested chain that actually has tracks)   #
    # ------------------------------------------------------------------ #

    def _most_niche_genre(self, session):
        genres_with_tracks = session.query(Genre).join(TrackGenre, Genre.genre_id == TrackGenre.genre_id).distinct().all()
        if not genres_with_tracks:
            return None
        deepest = max(genres_with_tracks, key=lambda g: g.depth)
        return {"name": deepest.genre_name, "depth": deepest.depth, "path": deepest.full_genre_path}

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
        return {"top": [(name, count) for name, count in rows[:limit]], "bottom": [(name, count) for name, count in rows[-limit:][::-1]]}

    # ------------------------------------------------------------------ #
    #  Genre timeline (album release years per genre)                     #
    # ------------------------------------------------------------------ #

    def _genre_timeline(self, session):
        """Album release-year view of each genre. An album has a genre when
        one or more of its tracks is tagged with it (no parent roll-up), and
        counts once per genre. Albums without a release_year are ignored."""
        rows = (
            session.query(Genre.genre_name, Album.album_id)
            .select_from(TrackGenre)
            .join(Genre, Genre.genre_id == TrackGenre.genre_id)
            .join(Track, Track.track_id == TrackGenre.track_id)
            .join(Album, Album.album_id == Track.album_id)
            .filter(Album.release_year.isnot(None))
            .distinct()
            .all()
        )
        album_ids_by_genre: dict[str, set[int]] = {}
        for genre_name, album_id in rows:
            album_ids_by_genre.setdefault(genre_name, set()).add(album_id)

        all_album_ids = set().union(*album_ids_by_genre.values()) if album_ids_by_genre else set()
        albums = {
            album.album_id: album for album in session.query(Album).options(selectinload(Album.album_roles).selectinload(AlbumRoleAssociation.role)).filter(Album.album_id.in_(all_album_ids)).all()
        }

        def describe(album):
            return {"album": album.album_name, "artist": album.album_artist_names, "date": _format_release_date(album.release_year, album.release_month, album.release_day)}

        year_distribution: dict[str, dict[int, int]] = {}
        earliest: dict[str, dict] = {}
        latest: dict[str, dict] = {}
        spans = []
        for genre_name, album_ids in album_ids_by_genre.items():
            genre_albums = [albums[album_id] for album_id in album_ids]
            counts: dict[int, int] = {}
            for album in genre_albums:
                counts[album.release_year] = counts.get(album.release_year, 0) + 1
            year_distribution[genre_name] = dict(sorted(counts.items()))
            earliest[genre_name] = describe(min(genre_albums, key=_earliest_key))
            latest[genre_name] = describe(min(genre_albums, key=_latest_key))
            if len(genre_albums) >= GENRE_SPAN_MIN_ALBUMS:
                first_year, last_year = min(counts), max(counts)
                spans.append((genre_name, last_year - first_year, first_year, last_year, len(genre_albums)))

        spans.sort(key=lambda r: r[0])
        return {"year_distribution": year_distribution, "earliest": earliest, "latest": latest, "spans": spans}

    # ------------------------------------------------------------------ #
    #  Highest/lowest rated mood (outlier-controlled)                     #
    # ------------------------------------------------------------------ #

    def _mood_ratings(self, session):
        moods = session.query(Mood).all()
        results = []
        for mood in moods:
            ratings = [t.user_rating for t in mood.tracks if t.user_rating is not None and RATING_MIN <= t.user_rating <= RATING_MAX]
            avg = outlier_controlled_average(ratings)
            if avg is not None:
                results.append((mood.mood_name, round(avg, 2), len(ratings)))

        results.sort(key=lambda r: r[1], reverse=True)
        return {"highest": results[:5], "lowest": results[-5:][::-1] if results else []}

    # ------------------------------------------------------------------ #
    #  Most / least played mood                                           #
    # ------------------------------------------------------------------ #

    def _mood_play_counts(self, session, limit=5):
        rows = (
            session.query(Mood.mood_name, func.coalesce(func.sum(Track.play_count), 0).label("plays"))
            .select_from(Mood)
            .join(MoodTrackAssociation, Mood.mood_id == MoodTrackAssociation.mood_id)
            .join(Track, MoodTrackAssociation.track_id == Track.track_id)
            .group_by(Mood.mood_id, Mood.mood_name)
            .order_by(func.coalesce(func.sum(Track.play_count), 0).desc())
            .all()
        )
        return {"most_played": [(name, plays) for name, plays in rows[:limit]], "least_played": [(name, plays) for name, plays in rows[-limit:][::-1]]}

    # ------------------------------------------------------------------ #
    #  Most representative tracks per (auto-tagged) mood                  #
    # ------------------------------------------------------------------ #

    def _representative_tracks_per_mood(self, session, limit=5):
        """{mood_name: [(track_name, primary_artist_names, score), ...]} --
        the `limit` tracks whose lyrics match each mood's keyword list most
        strongly, ranked by the persisted MoodTrackAssociation.score
        (lyrics-match density written at auto-tag time).

        Only rows with score > 0 count, so moods that were never auto-
        matched on any track (manual-only moods, or moods whose keyword
        list nothing hits) simply don't appear. Tracks with fewer than
        REPRESENTATIVE_MIN_TOKENS lyric tokens are skipped, and a mood
        left with nothing after that filter is dropped from the result.
        Ties broken by track_name for run-to-run stable ordering.
        """
        rows = (
            session.query(Mood.mood_name, MoodTrackAssociation.score, Track)
            .select_from(MoodTrackAssociation)
            .join(Mood, Mood.mood_id == MoodTrackAssociation.mood_id)
            .join(Track, Track.track_id == MoodTrackAssociation.track_id)
            .filter(MoodTrackAssociation.score.isnot(None))
            .filter(MoodTrackAssociation.score > 0)
            .order_by(Mood.mood_name, MoodTrackAssociation.score.desc(), Track.track_name.asc())
            .all()
        )

        result: dict[str, list] = {}
        for mood_name, score, track in rows:
            bucket = result.setdefault(mood_name, [])
            if len(bucket) >= limit:
                continue
            if len(_tokenize(track.lyrics or "")) < REPRESENTATIVE_MIN_TOKENS:
                continue
            bucket.append((track.track_name, track.primary_artist_names, score))
        return {mood_name: rows for mood_name, rows in result.items() if rows}
