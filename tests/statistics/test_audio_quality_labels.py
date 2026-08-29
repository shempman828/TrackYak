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


@pytest.fixture
def dialog(qapp, monkeypatch):
    for name in _LOADERS:
        monkeypatch.setattr(MusicStatsDialog, name, lambda self: None)
    d = MusicStatsDialog(controller=object())
    yield d
    d.close()
    d.deleteLater()


def test_on_stats_loaded_wires_in_the_audio_quality_loader(dialog):
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
        setattr(dialog, name, lambda: None)
    dialog.load_audio_quality_labels = lambda: called.append(True)

    dialog.on_stats_loaded({"total_tracks": 0, "audio_quality_stats": {}})

    assert called == [True]


def test_audio_quality_labels_populate_from_stats(dialog):
    dialog.stats = {
        "total_tracks": 1000,
        "audio_quality_stats": {
            "average_bit_rate": 290.0,
            "average_bit_depth": 17.0,
            "average_file_size": 8_388_608,  # 8 MiB
            "average_duration": 240.0,
        },
    }

    dialog.load_audio_quality_labels()

    assert "290.0" in dialog.avg_bit_rate_label.text()
    assert "kbps" in dialog.avg_bit_rate_label.text()
    assert "17.0" in dialog.avg_bit_depth_label.text()
    assert "bits" in dialog.avg_bit_depth_label.text()
    assert "8.00 MB" in dialog.avg_file_size_label.text()
    # 240s * 1000 tracks = 240000s -> format_duration -> "2d 18h"
    assert "2d 18h" in dialog.total_track_length_label.text()


def test_missing_audio_quality_stats_falls_back_to_na(dialog):
    dialog.stats = {"total_tracks": 0}

    dialog.load_audio_quality_labels()

    assert "N/A" in dialog.avg_bit_rate_label.text()
    assert "N/A" in dialog.avg_bit_depth_label.text()
    assert "N/A" in dialog.avg_file_size_label.text()
    # No duration data -> format_duration(0) -> "0s"
    assert "0s" in dialog.total_track_length_label.text()
