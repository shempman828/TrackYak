"""Tests for score_moods() (src/mood/mood_scoring.py), the scoring
engine from docs/specs/lyrics_mood_tagging.md. Each test maps 1:1 to a
numbered acceptance criterion.

Follows tests/core/test_censor_explicit_match.py's pattern: point the
module's cached-file path at a throwaway tmp_path file so these tests
don't depend on -- or break when someone edits -- the real, user-editable
assets/mood_keywords.json.
"""

import json

import pytest

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

    # Isolate from the real, user-editable assets/mood_opposites.json too --
    # otherwise a real opposite pair (e.g. Happy/Sad) would silently affect
    # tests that don't expect opposite-pair suppression at all. Points at a
    # nonexistent path by default (no pairs); test_opposite_pair_* below
    # override this with their own file via _isolated_opposites.
    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", tmp_path / "no_opposites.json")
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._cache["mtime"] = None
    mood_scoring._cache["keyword_patterns"] = None
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


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


# known_mood_names() ----------------------------------------------------------
def test_known_mood_names_returns_keyword_file_keys():
    assert mood_scoring.known_mood_names() == {"Happy", "Sad"}


# Opposite-mood pair resolution ------------------------------------------------
@pytest.fixture
def _isolated_opposites(tmp_path, monkeypatch):
    opposites_path = tmp_path / "mood_opposites.json"
    opposites_path.write_text(json.dumps([["Happy", "Sad"]]))
    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", opposites_path)
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None
    yield
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None


def test_opposite_pair_suppresses_the_lower_density_mood(_isolated_opposites):
    # Overwhelmingly Sad, with just enough incidental Happy hits (2 distinct
    # keywords) to clear Happy's own threshold on its own merits.
    lyrics = (
        "crying tears lonely crying tears lonely crying tears lonely "
        "crying tears lonely crying tears lonely happy sunshine"
    )
    result = mood_scoring.score_moods(lyrics)
    assert "Sad" in result
    assert "Happy" not in result


def test_non_opposite_moods_still_multi_label(tmp_path, monkeypatch):
    # Sanity check the pair mechanism doesn't disturb AC5 when the two
    # matched moods aren't a declared opposite pair -- an unrelated pair
    # in the file must not affect them.
    opposites_path = tmp_path / "mood_opposites.json"
    opposites_path.write_text(json.dumps([["Angry", "Chill"]]))
    monkeypatch.setattr(mood_scoring, "_OPPOSITES_PATH", opposites_path)
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None

    lyrics = "sunshine and joyful feelings, but also crying and tears and lonely nights"
    result = mood_scoring.score_moods(lyrics)
    assert "Happy" in result
    assert "Sad" in result


def test_opposite_pair_exact_tie_keeps_both(_isolated_opposites):
    lyrics = "happy sunshine crying tears"
    result = mood_scoring.score_moods(lyrics)
    assert "Happy" in result
    assert "Sad" in result


def test_missing_opposites_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mood_scoring, "_OPPOSITES_PATH", tmp_path / "does_not_exist.json"
    )
    mood_scoring._opposites_cache["mtime"] = None
    mood_scoring._opposites_cache["pairs"] = None

    lyrics = "sunshine and joyful feelings, but also crying and tears and lonely nights"
    result = mood_scoring.score_moods(lyrics)
    assert "Happy" in result
    assert "Sad" in result


# score_moods_detailed() -----------------------------------------------------
# (docs/specs/mood_representative_tracks.md -- AC1, AC2)

_DETAILED_FIXTURES = [
    "happy happy happy all day long, nothing but happy vibes here",
    "the sunshine feels joyful this morning as I walk outside",
    "sunshine and joyful feelings, but also crying and tears and lonely nights",
    "I am so happy today walking down the street feeling fine",  # below threshold
    "",
    "   ",
]


@pytest.mark.parametrize("lyrics", _DETAILED_FIXTURES)
def test_detailed_keys_match_score_moods(lyrics):
    # AC2 regression guard: the wrapper must stay exactly the key list.
    assert list(mood_scoring.score_moods_detailed(lyrics)) == mood_scoring.score_moods(
        lyrics
    )


def test_detailed_values_expose_positive_density_and_hit_counts():
    # AC1: every matched mood carries a MoodMatch with density > 0 and the
    # hit counts that cleared the threshold.
    detail = mood_scoring.score_moods_detailed(
        "the sunshine feels joyful this morning as I walk outside"
    )
    assert set(detail) == {"Happy"}
    match = detail["Happy"]
    assert match.density > 0
    assert match.distinct_hits == 2  # "sunshine", "joyful"
    assert match.raw_hits == 2


def test_detailed_multi_label_and_opposite_suppression(_isolated_opposites):
    # AC1: opposite-pair resolution applies to the detailed result too --
    # the suppressed mood is absent from the dict, not just zeroed.
    lyrics = (
        "crying tears lonely crying tears lonely crying tears lonely "
        "crying tears lonely crying tears lonely happy sunshine"
    )
    detail = mood_scoring.score_moods_detailed(lyrics)
    assert "Sad" in detail
    assert "Happy" not in detail
    assert detail["Sad"].density > 0
