"""Bug: "average rating by generation - expand generation list".

ArtistStats._generation_ratings iterates a hardcoded GENERATIONS tuple.
It used to cover only Boomer..Gen Z (begin_year 1946-2012), so an artist
born in the Silent Generation (e.g. 1930) or Gen Alpha (e.g. 2015) was
silently dropped from the breakdown no matter how many rated tracks they
had. GENERATIONS now spans Progressive Generation..Gen Beta.

Runs against a real in-memory SQLite session so the subquery/join chain
in _generation_ratings actually executes.
"""

from itertools import pairwise

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.db_tables import Artist, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.statistics.stats.artists import GENERATIONS, RATING_BUCKET_MIN_N, ArtistStats
from src.statistics.stats.genres_moods import REPRESENTATIVE_MIN_TOKENS, GenreMoodStats
from src.statistics.stats.lyrics import WORD_CLOUD_TOP_N, LyricsStats


# ---- test_artist_generation_ratings.py ---------------------------------------
@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)


def _seed_generation(session, artist_name, begin_year, rating, n_tracks):
    if session.get(Role, 1) is None:
        session.add(Role(role_id=1, role_name="Primary Artist"))
        session.flush()
    artist = Artist(artist_name=artist_name, begin_year=begin_year)
    session.add(artist)
    session.flush()
    for _ in range(n_tracks):
        track = Track(track_name=f"{artist_name} track", user_rating=rating)
        session.add(track)
        session.flush()
        session.add(TrackArtistRole(track_id=track.track_id, artist_id=artist.artist_id, role_id=1))


def test_generations_tuple_is_contiguous_and_non_overlapping():
    for (_, _, prev_end), (_, next_start, _) in pairwise(GENERATIONS):
        assert next_start == prev_end + 1


def test_pre_boomer_and_post_gen_z_cohorts_are_reported(session_factory):
    session = session_factory()
    n = RATING_BUCKET_MIN_N + 2
    _seed_generation(session, "Silent Artist", 1930, 7.0, n)
    _seed_generation(session, "Boomer Artist", 1950, 6.0, n)
    _seed_generation(session, "Alpha Artist", 2015, 8.0, n)
    session.commit()
    session.close()

    stats = ArtistStats(session_factory)
    rows = stats.get_comprehensive_artist_stats()["generation_ratings"]
    by_label = {label: (avg, count) for label, avg, count in rows}

    assert by_label["Silent Generation"] == (7.0, n)
    assert by_label["Boomer"] == (6.0, n)
    assert by_label["Gen Alpha"] == (8.0, n)


def test_sparse_new_cohort_is_still_suppressed(session_factory):
    session = session_factory()
    _seed_generation(session, "Alpha Artist", 2015, 8.0, RATING_BUCKET_MIN_N - 1)
    session.commit()
    session.close()

    stats = ArtistStats(session_factory)
    rows = stats.get_comprehensive_artist_stats()["generation_ratings"]

    assert "Gen Alpha" not in {label for label, _avg, _n in rows}


# ---- test_lyrics_stats.py ----------------------------------------------------
# Tests for LyricsStats' word_cloud vs. word_suggestions split
# (src/statistics/stats/lyrics.py): the mood-tagging dialog's "Refresh
# Suggestions" appeared to do nothing once a user worked through the word
# list, because both the WordCloudWidget chart and the mood dialog's
# suggestion feed shared the same WORD_CLOUD_TOP_N-capped list -- a fixed,
# deterministic top-N that never grows no matter how many times it's
# refetched. word_suggestions exposes the full ranked list instead, while
# word_cloud stays capped for the chart.
class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)

    def close(self):
        pass


def _distinct_word(i: int) -> str:
    """An all-alphabetic, guaranteed-distinct token -- _WORD_RE only matches
    [a-zA-Z'], so a numeric suffix (e.g. "lexeme0") would have its digits
    silently dropped by tokenization and collide with every other index."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    suffix = "".join(letters[(i // (26**p)) % 26] for p in range(4))
    return f"lexeme{suffix}"


def _make_rows(word_count: int, tracks_per_word: int = 5):
    """`tracks_per_word` tracks, each one lyric line containing every one of
    `word_count` distinct words once -- so every word clears
    WORD_CLOUD_MIN_TRACKS (5) document frequency."""
    words = [_distinct_word(i) for i in range(word_count)]
    lyrics_line = " ".join(words)
    return [(lyrics_line, None) for _ in range(tracks_per_word)]


def test_word_cloud_is_unsliced_on_the_stats_object_directly():
    rows = _make_rows(WORD_CLOUD_TOP_N + 50)
    stats = LyricsStats(session_factory=None)

    full = stats._word_cloud(rows)

    assert len(full) == WORD_CLOUD_TOP_N + 50


def test_get_comprehensive_lyrics_stats_caps_word_cloud_but_not_suggestions():
    rows = _make_rows(WORD_CLOUD_TOP_N + 50)
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert len(result["word_cloud"]) == WORD_CLOUD_TOP_N
    assert len(result["word_suggestions"]) == WORD_CLOUD_TOP_N + 50
    # word_cloud is a prefix of word_suggestions -- same ranking, just capped.
    assert result["word_suggestions"][:WORD_CLOUD_TOP_N] == result["word_cloud"]


def test_word_suggestions_matches_word_cloud_when_under_the_cap():
    rows = _make_rows(20)
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert result["word_cloud"] == result["word_suggestions"]
    assert len(result["word_cloud"]) == 20


def test_phrase_candidates_surfaces_a_repeated_trigram():
    lyric = "you broke my heart and walked away, you broke my heart again"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    # "broke my" and "my heart" are each edge-anchored on "my", a stopword,
    # so only the full trigram (edges "broke"/"heart", both content words)
    # clears the boundary filter.
    assert "broke my heart" in phrases
    assert "broke my" not in phrases
    assert "my heart" not in phrases


def test_phrase_candidates_keeps_interior_stopword_phrases():
    # "state of mind" -- edges ("state"/"mind") are content words even
    # though the phrase's interior word ("of") is a stopword.
    lyric = "living in a state of mind tonight, dreaming in a state of mind"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    assert "state of mind" in phrases
    assert "of mind" not in phrases  # edge-anchored on "of", a stopword


def test_phrase_candidates_drops_stopword_anchored_fragments():
    lyric = "and i walked away and i cried and i walked away and i cried"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    # "and i", "i walked" (starts on "i", a stopword) shouldn't appear --
    # only content-anchored phrases like "walked away" should.
    assert "and i" not in phrases
    assert "i walked" not in phrases
    assert "walked away" in phrases


def test_phrase_candidates_respects_min_tracks_threshold():
    # Appears in only 4 tracks -- below PHRASE_MIN_TRACKS (5).
    rows = [("broke my heart tonight", None)] * 4

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    assert "broke my" not in phrases


def test_get_comprehensive_lyrics_stats_includes_phrase_suggestions():
    lyric = "broke my heart again, broke my heart tonight"
    rows = [(lyric, None)] * 5
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert "phrase_suggestions" in result
    assert dict(result["phrase_suggestions"])["broke my heart"] == 10


# ---- test_mood_representative_tracks.py --------------------------------------
# Tests for GenreMoodStats._representative_tracks_per_mood()
# (src/statistics/stats/genres_moods.py) -- the "5 most representative
# tracks per auto-tagged mood" statistic from
# docs/specs/mood_representative_tracks.md. Each test maps to a numbered
# acceptance criterion.
#
# Ranks on the persisted MoodTrackAssociation.score (lyrics-match density),
# so these tests write scores directly rather than going through the
# scoring engine.
_LONG_LYRIC = " ".join(f"lyricword{i}" for i in range(REPRESENTATIVE_MIN_TOKENS + 10))

_SHORT_LYRIC = "one two three four five"


@pytest.fixture
def Session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def seed(Session):
    """seed({"Happy": [("Song A", 0.05), ...], ...}) -> creates the moods,
    the tracks, and one scored association per (mood, track) pair.

    Entries are (track_name, score) or (track_name, score, lyrics); the
    lyric defaults to one well over the min-token floor. score None writes
    a NULL score; a track name may repeat across moods."""

    def _seed(spec):
        s = Session()
        moods = {}
        tracks = {}
        for mood_name, entries in spec.items():
            mood = Mood(mood_name=mood_name)
            s.add(mood)
            s.flush()
            moods[mood_name] = mood
            for entry in entries:
                track_name, score = entry[0], entry[1]
                lyrics = entry[2] if len(entry) > 2 else _LONG_LYRIC
                track = tracks.get(track_name)
                if track is None:
                    track = Track(track_name=track_name, lyrics=lyrics)
                    s.add(track)
                    s.flush()
                    tracks[track_name] = track
                s.add(
                    MoodTrackAssociation(mood_id=mood.mood_id, track_id=track.track_id, score=score)
                )
        s.commit()
        s.close()

    return _seed


def _stats(Session):
    return GenreMoodStats(Session).get_comprehensive_genre_mood_stats()[
        "representative_tracks_per_mood"
    ]


def test_returns_top_5_ordered_by_score_desc(Session, seed):
    seed({"Happy": [(f"Song {i}", 0.01 * i) for i in range(1, 8)]})  # 7 tracks
    result = _stats(Session)

    assert list(result) == ["Happy"]
    names = [name for name, _artist, _score in result["Happy"]]
    scores = [score for _name, _artist, score in result["Happy"]]
    assert names == ["Song 7", "Song 6", "Song 5", "Song 4", "Song 3"]
    assert scores == sorted(scores, reverse=True)
    assert len(result["Happy"]) == 5


def test_null_and_zero_scores_are_excluded(Session, seed):
    seed({"Happy": [("Real Match", 0.04), ("Null Score", None), ("Zero Score", 0.0)]})
    result = _stats(Session)

    assert [name for name, _a, _s in result["Happy"]] == ["Real Match"]


def test_score_ties_broken_by_track_name(Session, seed):
    seed({"Happy": [("Zulu", 0.02), ("Alpha", 0.02), ("Mike", 0.02)]})
    result = _stats(Session)

    assert [name for name, _a, _s in result["Happy"]] == ["Alpha", "Mike", "Zulu"]


def test_mood_with_no_positive_score_row_is_absent(Session, seed):
    seed({"Happy": [("Good One", 0.03)], "Sad": [("Manual Only", None), ("Also Manual", 0.0)]})
    result = _stats(Session)

    assert "Sad" not in result
    assert "Happy" in result


def test_entry_shape_is_name_artist_score(Session, seed):
    seed({"Happy": [("Solo Track", 0.123)]})
    result = _stats(Session)

    entry = result["Happy"][0]
    assert entry == ("Solo Track", "Unknown Artist", pytest.approx(0.123))
    assert entry[2] > 0  # density can legitimately exceed 1.0 (>100%)


def test_short_lyric_tracks_are_excluded(Session, seed):
    seed({"Happy": [("Tiny But Dense", 0.9, _SHORT_LYRIC), ("Proper Song", 0.2)]})
    result = _stats(Session)

    # The higher score belongs to the sub-floor lyric -- it's dropped, and
    # the full-length song stands alone.
    assert [name for name, _a, _s in result["Happy"]] == ["Proper Song"]


def test_mood_with_only_short_lyric_tracks_is_absent(Session, seed):
    seed({"Happy": [("Chant", 0.9, _SHORT_LYRIC)]})
    assert _stats(Session) == {}


def test_no_qualifying_rows_yields_empty_mapping(Session, seed):
    seed({"Happy": [("Nope", None)]})
    assert _stats(Session) == {}
