"""The Audio Profile tab's "Audio Quality" group (Average Bit Rate / Bit
Depth / File Size / Total Track Length).

These four labels are created in create_audio_profile_tab() but fed from the
*comprehensive* stats payload (self.stats["audio_quality_stats"]), not the
lazy AudioStatsWorker that drives the tab's charts. The 8-tab rebuild
(commit 722d849) dropped the populate call, leaving them as bare captions
with no values -- this pins load_audio_quality_labels() back into
on_stats_loaded()'s loader chain.
"""

import pytest

from src.statistics.statistics_dialog import MusicStatsDialog

# ---- test_audio_quality_labels.py --------------------------------------------
_LOADERS_aql = (
    "load_data",
    "_load_influence_tiles",
    "_load_audio_stats",
    "_load_genre_mood_stats",
    "_load_album_stats",
    "_load_artist_stats",
    "_load_places_credits_stats",
    "_load_lyrics_stats",
)


@pytest.fixture
def dialog_aql(qapp, monkeypatch):
    for name in _LOADERS_aql:
        monkeypatch.setattr(MusicStatsDialog, name, lambda self: None)
    d = MusicStatsDialog(controller=object())
    yield d
    d.close()
    d.deleteLater()


def test_on_stats_loaded_wires_in_the_audio_quality_loader(dialog_aql):
    """The regression itself: on_stats_loaded() must reach
    load_audio_quality_labels() (it was dropped from the loader chain)."""
    called = []
    for name in (
        "load_overview_data",
        "load_library_health_data",
        "load_artists_data",
        "load_albums_data",
        "load_genres_moods_data",
    ):
        setattr(dialog_aql, name, lambda: None)
    dialog_aql.load_audio_quality_labels = lambda: called.append(True)

    dialog_aql.on_stats_loaded({"total_tracks": 0, "audio_quality_stats": {}})

    assert called == [True]


def test_audio_quality_labels_populate_from_stats(dialog_aql):
    dialog_aql.stats = {
        "total_tracks": 1000,
        "audio_quality_stats": {
            "average_bit_rate": 290.0,
            "average_bit_depth": 17.0,
            "average_file_size": 8_388_608,  # 8 MiB
            "average_duration": 240.0,
        },
    }

    dialog_aql.load_audio_quality_labels()

    assert "290.0" in dialog_aql.avg_bit_rate_label.text()
    assert "kbps" in dialog_aql.avg_bit_rate_label.text()
    assert "17.0" in dialog_aql.avg_bit_depth_label.text()
    assert "bits" in dialog_aql.avg_bit_depth_label.text()
    assert "8.00 MB" in dialog_aql.avg_file_size_label.text()
    # 240s * 1000 tracks = 240000s -> format_duration -> "2d 18h"
    assert "2d 18h" in dialog_aql.total_track_length_label.text()


def test_missing_audio_quality_stats_falls_back_to_na(dialog_aql):
    dialog_aql.stats = {"total_tracks": 0}

    dialog_aql.load_audio_quality_labels()

    assert "N/A" in dialog_aql.avg_bit_rate_label.text()
    assert "N/A" in dialog_aql.avg_bit_depth_label.text()
    assert "N/A" in dialog_aql.avg_file_size_label.text()
    # No duration data -> format_duration(0) -> "0s"
    assert "0s" in dialog_aql.total_track_length_label.text()


# ---- test_statistics_dialog_representative_moods.py --------------------------
# UI wiring for the "Most Representative Tracks per Mood" group on the
# Genres & Moods stats tab (docs/specs/mood_representative_tracks.md,
# AC13/AC14/AC16).
#
# The dialog_rm's __init__ fires eight background stat workers; each is
# monkeypatched to a no-op so the test drives the render path directly with
# a hand-built stats payload.
_LOADERS_rm = (
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
def dialog_rm(qapp, monkeypatch):
    for name in _LOADERS_rm:
        monkeypatch.setattr(MusicStatsDialog, name, lambda self: None)
    d = MusicStatsDialog(controller=object())
    yield d
    d.close()
    d.deleteLater()


def _rows(widget):
    return [(name, value, secondary) for name, value, secondary in widget._rows]


def test_combo_populated_and_first_mood_shown_on_load(dialog_rm):
    dialog_rm.genre_mood_stats = {
        **_PHASE3_STUB,
        "representative_tracks_per_mood": {
            "Sad": [("Blue", "Joni", 0.031)],
            "Happy": [("Sunny", "Stevie", 0.052), ("Bright", "Ray", 0.021)],
        },
    }

    dialog_rm.load_genre_mood_phase3_data()

    combo = dialog_rm.representative_mood_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Happy", "Sad"]
    assert combo.isEnabled()
    # First mood (alphabetical) shown, values rendered as percentages.
    assert _rows(dialog_rm.representative_tracks_list) == [
        ("Sunny", 5.2, "Stevie"),
        ("Bright", 2.1, "Ray"),
    ]


def test_changing_combo_swaps_the_list(dialog_rm):
    dialog_rm.genre_mood_stats = {
        **_PHASE3_STUB,
        "representative_tracks_per_mood": {
            "Happy": [("Sunny", "Stevie", 0.052)],
            "Sad": [("Blue", "Joni", 0.031)],
        },
    }
    dialog_rm.load_genre_mood_phase3_data()

    dialog_rm.representative_mood_combo.setCurrentText("Sad")

    assert _rows(dialog_rm.representative_tracks_list) == [("Blue", 3.1, "Joni")]


def test_no_qualifying_mood_disables_combo_and_clears_list(dialog_rm):
    dialog_rm.genre_mood_stats = {**_PHASE3_STUB, "representative_tracks_per_mood": {}}

    dialog_rm.load_genre_mood_phase3_data()

    assert dialog_rm.representative_mood_combo.count() == 0
    assert not dialog_rm.representative_mood_combo.isEnabled()
    assert dialog_rm.representative_tracks_list._rows == []
