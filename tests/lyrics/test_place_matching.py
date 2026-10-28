"""Tests for detect_known_places() (src/lyrics/autotag/place_matching.py), the
DB-only place-name-drop matcher from docs/specs/lyrics_mood_tagging.md.
Confirms no gazetteer/new-Place-row behavior is involved -- this module
only ever matches against names it's explicitly given.
"""

import pytest

from src.lyrics.autotag.place_matching import detect_known_places


@pytest.mark.parametrize(
    ("lyrics", "known", "expected"),
    [
        ("I left my heart in Paris one cold December night", ["Paris", "Tokyo"], ["Paris"]),
        ("I left my heart in Jamaica under the sun", ["Paris", "Tokyo"], []),  # never invents a place
        ("we flew into TOKYO last night and the city was alive", ["Tokyo"], ["Tokyo"]),
        ("the London Bridge is falling, or so the song goes", ["Ondon"], []),  # no substring match
        ("", ["Paris"], []),
        (None, ["Paris"], []),
        ("Paris Tokyo London", [], []),
        ("we drove all night to Washington, D.C. and back", ["Washington, D.C."], ["Washington, D.C."]),
        ("St. Louisville is far", ["St. Louis"], []),
        ("any lyrics at all", ["", "   "], []),
        ("from Tokyo to Paris and Tokyo again", ["Tokyo", "Paris", "Tokyo"], ["Tokyo", "Paris"]),
    ],
)
def test_detect_known_places(lyrics, known, expected):
    assert detect_known_places(lyrics, known) == expected
