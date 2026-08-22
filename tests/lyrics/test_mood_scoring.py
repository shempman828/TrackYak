"""Tests for score_moods() (src/lyrics/mood_scoring.py), the scoring
engine from docs/specs/lyrics_mood_tagging.md. Each test maps 1:1 to a
numbered acceptance criterion.

Follows tests/core/test_censor_explicit_match.py's pattern: point the
module's cached-file path at a throwaway tmp_path file so these tests
don't depend on -- or break when someone edits -- the real, user-editable
assets/mood_keywords.json.
"""

import json

import pytest

from src.lyrics import mood_scoring


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
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None


# AC1 -------------------------------------------------------------------------
def test_single_mention_does_not_tag():
    lyrics = "I am so happy today walking down the street feeling fine"
    assert "Happy" not in mood_scoring.score_moods(lyrics)


# AC2 -------------------------------------------------------------------------
def test_repeated_single_keyword_tags():
    lyrics = "happy happy happy all day long, nothing but happy vibes here"
    assert "Happy" in mood_scoring.score_moods(lyrics)


# AC3 -------------------------------------------------------------------------
def test_two_distinct_keywords_once_each_tags():
    lyrics = "the sunshine feels joyful this morning as I walk outside"
    assert "Happy" in mood_scoring.score_moods(lyrics)


# AC4 -------------------------------------------------------------------------
def test_density_floor_blocks_sparse_matches_in_long_lyrics():
    filler = " ".join(f"filler{i}" for i in range(700))
    lyrics = f"{filler} happy happy happy {filler}"
    assert "Happy" not in mood_scoring.score_moods(lyrics)


# AC5 -------------------------------------------------------------------------
def test_multi_label_tags_two_different_moods():
    lyrics = "sunshine and joyful feelings, but also crying and tears and lonely nights"
    result = mood_scoring.score_moods(lyrics)
    assert "Happy" in result
    assert "Sad" in result


# AC6 -------------------------------------------------------------------------
def test_whole_word_and_case_insensitive():
    # Substring collision: "sunshine" must not match inside "sunshiners"
    # (made-up word) or similar -- whole-word only.
    lyrics = "SUNSHINE and JOYFUL are both capitalized here"
    assert "Happy" in mood_scoring.score_moods(lyrics)

    no_match = "sunshiners joyfulness are not the real keywords at all here"
    assert "Happy" not in mood_scoring.score_moods(no_match)


def test_empty_lyrics_scores_nothing():
    assert mood_scoring.score_moods("") == []
    assert mood_scoring.score_moods(None) == []
    assert mood_scoring.score_moods("   ") == []
