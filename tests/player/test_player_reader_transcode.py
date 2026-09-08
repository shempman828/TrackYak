"""Regression: every extension the importer accepts must be *playable* --
either the linked libsndfile opens it directly, or the reader routes it
through ffmpeg first.

The bug: `.aac` / `.opus` / `.ogg` were listed in
`TrackImporter.SUPPORTED_EXTENSIONS`, so they imported into the library, but
the reader only transcoded `.m4a`. libsndfile was then handed raw ADTS / an
Ogg stream it couldn't decode and playback failed with no useful message --
and `.aac`/`.opus` were additionally rejected at the player's load gate
before the ffmpeg path could even be reached.
"""

from pathlib import Path

import pytest
import soundfile as sf

from src.importing.library_import import TrackImporter
from src.player import player_reader
from src.player.player_reader import (
    _FFMPEG_TRANSCODE_FORMATS,
    _LIBSNDFILE_FORMATS,
    _PLAYABLE_EXTENSIONS,
    TranscodeUnavailableError,
    _libsndfile_decodable_extensions,
    _transcode_to_wav,
)
from src.player.player_track_loading import SUPPORTED_FORMATS


def test_every_imported_extension_is_playable():
    """The core assertion from the bug report."""
    for ext in TrackImporter.SUPPORTED_EXTENSIONS:
        assert ext in _LIBSNDFILE_FORMATS or ext in _FFMPEG_TRANSCODE_FORMATS, (
            f"{ext} is importable but neither libsndfile-decodable nor "
            f"routed through ffmpeg -- it will import and fail to play"
        )


def test_importer_extensions_pass_the_player_load_gate():
    # load_track()/_start_preload_next() reject anything not in SUPPORTED_FORMATS
    # before _open_soundfile() is ever called.
    assert TrackImporter.SUPPORTED_EXTENSIONS <= SUPPORTED_FORMATS


def test_native_and_transcode_sets_partition_the_playable_set():
    assert _LIBSNDFILE_FORMATS.isdisjoint(_FFMPEG_TRANSCODE_FORMATS)
    assert _LIBSNDFILE_FORMATS | _FFMPEG_TRANSCODE_FORMATS == _PLAYABLE_EXTENSIONS


def test_mpeg4_and_raw_aac_always_transcode():
    # libsndfile has never decoded MPEG-4 containers or raw ADTS AAC,
    # regardless of build options.
    assert ".m4a" in _FFMPEG_TRANSCODE_FORMATS
    assert ".aac" in _FFMPEG_TRANSCODE_FORMATS
    assert ".m4a" not in _LIBSNDFILE_FORMATS
    assert ".aac" not in _LIBSNDFILE_FORMATS


def test_detection_tracks_the_installed_libsndfile():
    detected = _libsndfile_decodable_extensions()
    formats = {name.upper() for name in sf.available_formats()}
    subtypes = {name.upper() for name in sf.available_subtypes()}
    assert (".ogg" in detected) == ("OGG" in formats and "VORBIS" in subtypes)
    assert (".opus" in detected) == ("OGG" in formats and "OPUS" in subtypes)


def test_missing_ffmpeg_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(player_reader.shutil, "which", lambda _name: None)
    with pytest.raises(TranscodeUnavailableError) as excinfo:
        _transcode_to_wav(Path("/music/song.aac"))
    message = str(excinfo.value).lower()
    assert "ffmpeg" in message
    assert ".aac" in message
