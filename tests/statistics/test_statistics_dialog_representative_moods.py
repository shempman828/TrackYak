"""UI wiring for the "Most Representative Tracks per Mood" group on the
Genres & Moods stats tab (docs/specs/mood_representative_tracks.md,
AC13/AC14/AC16).

The dialog's __init__ fires eight background stat workers; each is
monkeypatched to a no-op so the test drives the render path directly with
a hand-built stats payload.
"""

import pytest

from src.statistics.statistics_dialog import MusicStatsDialog

_LOADERS = (
    "load_data",
    "_load_influence_tiles",
    "_load_audio_stats",
    "_load_genre_mood_stats",
    "_load_album_stats",
    "_load_artist_stats",
    "_load_places_credits_stats",
    "_load_lyrics_stats",
)

_PHASE3_STUB = {
    "rated_genres_leaderboard": {"highest": {}, "lowest": {}},
    "most_niche_genre": None,
    "genres_by_track_count": {"top": [], "bottom": []},
    "mood_ratings_outlier_controlled": {"highest": [], "lowest": []},
    "mood_play_counts": {"most_played": [], "least_played": []},
}


@pytest.fixture
def dialog(qapp, monkeypatch):
    for name in _LOADERS:
        monkeypatch.setattr(MusicStatsDialog, name, lambda self: None)
    d = MusicStatsDialog(controller=object())
    yield d
    d.close()
    d.deleteLater()


def _rows(widget):
    return [(name, value, secondary) for name, value, secondary in widget._rows]


# AC13 --------------------------------------------------------------------
def test_combo_populated_and_first_mood_shown_on_load(dialog):
    dialog.genre_mood_stats = {
        **_PHASE3_STUB,
        "representative_tracks_per_mood": {
            "Sad": [("Blue", "Joni", 0.031)],
            "Happy": [("Sunny", "Stevie", 0.052), ("Bright", "Ray", 0.021)],
        },
    }

    dialog.load_genre_mood_phase3_data()

    combo = dialog.representative_mood_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Happy", "Sad"]
    assert combo.isEnabled()
    # First mood (alphabetical) shown, values rendered as percentages.
    assert _rows(dialog.representative_tracks_list) == [
        ("Sunny", 5.2, "Stevie"),
        ("Bright", 2.1, "Ray"),
    ]


# AC14 --------------------------------------------------------------------
def test_changing_combo_swaps_the_list(dialog):
    dialog.genre_mood_stats = {
        **_PHASE3_STUB,
        "representative_tracks_per_mood": {
            "Happy": [("Sunny", "Stevie", 0.052)],
            "Sad": [("Blue", "Joni", 0.031)],
        },
    }
    dialog.load_genre_mood_phase3_data()

    dialog.representative_mood_combo.setCurrentText("Sad")

    assert _rows(dialog.representative_tracks_list) == [("Blue", 3.1, "Joni")]


# AC16 --------------------------------------------------------------------
def test_no_qualifying_mood_disables_combo_and_clears_list(dialog):
    dialog.genre_mood_stats = {
        **_PHASE3_STUB,
        "representative_tracks_per_mood": {},
    }

    dialog.load_genre_mood_phase3_data()

    assert dialog.representative_mood_combo.count() == 0
    assert not dialog.representative_mood_combo.isEnabled()
    assert dialog.representative_tracks_list._rows == []
