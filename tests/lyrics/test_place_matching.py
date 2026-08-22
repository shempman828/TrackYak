"""Tests for detect_known_places() (src/lyrics/place_matching.py), the
DB-only place-name-drop matcher from docs/specs/lyrics_mood_tagging.md.
Confirms no gazetteer/new-Place-row behavior is involved -- this module
only ever matches against names it's explicitly given.
"""

from src.lyrics.place_matching import detect_known_places


# AC7 -------------------------------------------------------------------------
def test_matches_a_known_place_name():
    lyrics = "I left my heart in Paris one cold December night"
    assert detect_known_places(lyrics, ["Paris", "Tokyo"]) == ["Paris"]


# AC8 -------------------------------------------------------------------------
def test_place_not_in_known_list_never_matches():
    lyrics = "I left my heart in Jamaica under the sun"
    # "Jamaica" is not in the known list -- must not be invented/matched.
    assert detect_known_places(lyrics, ["Paris", "Tokyo"]) == []


def test_whole_word_case_insensitive_match():
    lyrics = "we flew into TOKYO last night and the city was alive"
    assert detect_known_places(lyrics, ["Tokyo"]) == ["Tokyo"]


def test_substring_does_not_false_positive():
    lyrics = "the London Bridge is falling, or so the song goes"
    # "Ondon" as a made-up place name must not match inside "London".
    assert detect_known_places(lyrics, ["Ondon"]) == []


def test_empty_lyrics_matches_nothing():
    assert detect_known_places("", ["Paris"]) == []
    assert detect_known_places(None, ["Paris"]) == []


def test_no_known_places_matches_nothing():
    assert detect_known_places("Paris Tokyo London", []) == []
