"""Tests for GenreMoodStats._representative_tracks_per_mood()
(src/statistics/stats/genres_moods.py) -- the "5 most representative
tracks per auto-tagged mood" statistic from
docs/specs/mood_representative_tracks.md. Each test maps to a numbered
acceptance criterion.

Ranks on the persisted MoodTrackAssociation.score (lyrics-match density),
so these tests write scores directly rather than going through the
scoring engine.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.statistics.stats.genres_moods import (
    REPRESENTATIVE_MIN_TOKENS,
    GenreMoodStats,
)

# A lyric comfortably over REPRESENTATIVE_MIN_TOKENS distinct content tokens.
_LONG_LYRIC = " ".join(f"lyricword{i}" for i in range(REPRESENTATIVE_MIN_TOKENS + 10))
# A lyric that tokenizes to just a handful of tokens -- below the floor.
_SHORT_LYRIC = "one two three four five"


@pytest.fixture
def Session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
                    MoodTrackAssociation(
                        mood_id=mood.mood_id, track_id=track.track_id, score=score
                    )
                )
        s.commit()
        s.close()

    return _seed


def _stats(Session):
    return GenreMoodStats(Session).get_comprehensive_genre_mood_stats()[
        "representative_tracks_per_mood"
    ]


# AC8 -----------------------------------------------------------------------
def test_returns_top_5_ordered_by_score_desc(Session, seed):
    seed({"Happy": [(f"Song {i}", 0.01 * i) for i in range(1, 8)]})  # 7 tracks
    result = _stats(Session)

    assert list(result) == ["Happy"]
    names = [name for name, _artist, _score in result["Happy"]]
    scores = [score for _name, _artist, score in result["Happy"]]
    assert names == ["Song 7", "Song 6", "Song 5", "Song 4", "Song 3"]
    assert scores == sorted(scores, reverse=True)
    assert len(result["Happy"]) == 5


# AC9 -----------------------------------------------------------------------
def test_null_and_zero_scores_are_excluded(Session, seed):
    seed(
        {
            "Happy": [
                ("Real Match", 0.04),
                ("Null Score", None),
                ("Zero Score", 0.0),
            ]
        }
    )
    result = _stats(Session)

    assert [name for name, _a, _s in result["Happy"]] == ["Real Match"]


# AC10 ----------------------------------------------------------------------
def test_score_ties_broken_by_track_name(Session, seed):
    seed({"Happy": [("Zulu", 0.02), ("Alpha", 0.02), ("Mike", 0.02)]})
    result = _stats(Session)

    assert [name for name, _a, _s in result["Happy"]] == ["Alpha", "Mike", "Zulu"]


# AC11 ----------------------------------------------------------------------
def test_mood_with_no_positive_score_row_is_absent(Session, seed):
    seed(
        {
            "Happy": [("Good One", 0.03)],
            "Sad": [("Manual Only", None), ("Also Manual", 0.0)],
        }
    )
    result = _stats(Session)

    assert "Sad" not in result
    assert "Happy" in result


# AC12 ----------------------------------------------------------------------
def test_entry_shape_is_name_artist_score(Session, seed):
    seed({"Happy": [("Solo Track", 0.123)]})
    result = _stats(Session)

    entry = result["Happy"][0]
    assert entry == ("Solo Track", "Unknown Artist", pytest.approx(0.123))
    assert entry[2] > 0  # density can legitimately exceed 1.0 (>100%)


# min-token guard ---------------------------------------------------------
def test_short_lyric_tracks_are_excluded(Session, seed):
    seed(
        {
            "Happy": [
                ("Tiny But Dense", 0.9, _SHORT_LYRIC),
                ("Proper Song", 0.2),
            ]
        }
    )
    result = _stats(Session)

    # The higher score belongs to the sub-floor lyric -- it's dropped, and
    # the full-length song stands alone.
    assert [name for name, _a, _s in result["Happy"]] == ["Proper Song"]


def test_mood_with_only_short_lyric_tracks_is_absent(Session, seed):
    seed({"Happy": [("Chant", 0.9, _SHORT_LYRIC)]})
    assert _stats(Session) == {}


# AC16 (data side) --------------------------------------------------------
def test_no_qualifying_rows_yields_empty_mapping(Session, seed):
    seed({"Happy": [("Nope", None)]})
    assert _stats(Session) == {}
