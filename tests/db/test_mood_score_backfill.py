"""Tests for scripts/backfill_mood_association_scores.py::backfill_scores()
-- the one-time catch-up that fills MoodTrackAssociation.score for rows
predating the column, per docs/specs/mood_representative_tracks.md
(AC6/AC7).

In-memory SQLite session, same shape as tests/mood/test_mood_tag_worker.py.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.backfill_mood_association_scores import backfill_scores
from src.db.db_tables.base import Base
from src.db.db_tables.mood import Mood, MoodTrackAssociation
from src.db.db_tables.track import Track
from src.mood import mood_scoring


@pytest.fixture(autouse=True)
def _isolated_keywords(tmp_path, monkeypatch):
    keywords_path = tmp_path / "mood_keywords.json"
    keywords_path.write_text(
        json.dumps(
            {
                "Happy": ["happy", "sunshine", "joyful"],
                "Sad": ["crying", "tears", "lonely"],
            }
        )
    )
    monkeypatch.setattr(mood_scoring, "_KEYWORDS_PATH", keywords_path)
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    monkeypatch.setattr(
        mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json"
    )
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _add(session, *, lyrics, mood_name="Happy", score=None):
    mood = session.query(Mood).filter_by(mood_name=mood_name).one_or_none()
    if mood is None:
        mood = Mood(mood_name=mood_name)
        session.add(mood)
    track = Track(track_name="T", lyrics=lyrics)
    session.add(track)
    session.flush()
    assoc = MoodTrackAssociation(
        mood_id=mood.mood_id, track_id=track.track_id, score=score
    )
    session.add(assoc)
    session.commit()
    return assoc


# AC6 --------------------------------------------------------------------
def test_fills_matching_row_with_density(session):
    lyrics = "happy happy happy sunshine joyful morning vibes"
    assoc = _add(session, lyrics=lyrics)

    scored, matched = backfill_scores(session, progress_every=0)

    assert (scored, matched) == (1, 1)
    expected = mood_scoring.score_moods_detailed(lyrics)["Happy"].density
    assert assoc.score == pytest.approx(expected)


# AC6 --------------------------------------------------------------------
def test_writes_zero_for_non_matching_manual_tag(session):
    assoc = _add(session, lyrics="a totally unrelated clean lyric line, no keywords")

    scored, matched = backfill_scores(session, progress_every=0)

    assert (scored, matched) == (1, 0)
    assert assoc.score == 0.0


# AC7 --------------------------------------------------------------------
def test_no_lyrics_row_left_null_and_second_run_is_a_noop(session):
    match = _add(session, lyrics="happy sunshine joyful happy")
    null_row = _add(session, lyrics=None, mood_name="Sad")

    scored, _ = backfill_scores(session, progress_every=0)
    assert scored == 1
    assert match.score is not None and match.score > 0
    assert null_row.score is None

    first = match.score
    scored_again, matched_again = backfill_scores(session, progress_every=0)
    assert (scored_again, matched_again) == (0, 0)
    assert match.score == pytest.approx(first)
    assert null_row.score is None


# AC5 (write-path boundary, exercised from the backfill side) -----------
def test_existing_non_null_score_is_never_recomputed(session):
    assoc = _add(session, lyrics="happy sunshine joyful happy", score=0.999)

    scored, matched = backfill_scores(session, progress_every=0)

    assert (scored, matched) == (0, 0)
    assert assoc.score == 0.999
