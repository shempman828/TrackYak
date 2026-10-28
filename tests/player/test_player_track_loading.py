"""Track loading: every importable extension is playable (transcode), and
damaged files (zero samplerate, corrupt next track) never crash loading.
"""

from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import soundfile as sf

from src.importing.library_import import TrackImporter
from src.player.engine import player_reader, player_track_loading
from src.player.engine.player_reader import _FFMPEG_TRANSCODE_FORMATS, _LIBSNDFILE_FORMATS, _PLAYABLE_EXTENSIONS, TranscodeUnavailableError, _libsndfile_decodable_extensions, _transcode_to_wav
from src.player.engine.player_track_loading import SUPPORTED_FORMATS, PlayerTrackLoadingMixin

# ---------------------------------------------------------------------------
# Regression: every extension the importer accepts must be *playable* --
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression: a damaged header reporting samplerate=0 must not crash load_track().
# ---------------------------------------------------------------------------


class _Sig:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakeReader:
    def __init__(self, samplerate):
        self.samplerate = samplerate
        self.channels = 2
        self.closed = False

    def __len__(self):
        return 1000

    def close(self):
        self.closed = True


class _Bare(PlayerTrackLoadingMixin):
    """Only the state load_track() touches before the samplerate check."""

    def __init__(self):
        self.sf = sf
        self.error_occurred = _Sig()
        self.position_changed = _Sig()
        self.track_changed = _Sig()
        self.duration_changed = _Sig()
        self._position_timer = SimpleNamespace(stop=lambda: None)
        self._finish_pending = threading.Event()
        self._has_reached_threshold = False
        self._play_count_recorded = False
        self._position = 0
        self._frames_played = 0
        self.playing = False
        self._preload_lock = threading.Lock()
        self._next_file = None
        self._next_sf_reader = None
        self._reader_lock = threading.Lock()
        self._sf_reader = None
        self._current_frame = 0
        self.current_sample_rate = 44100
        self.current_channels = 2
        self._total_frames = 0
        self._resolved_file_path = None
        self.current_format = None
        self.current_bit_depth = 32
        self._duration = 0

    def _stop_reader_thread(self):
        pass

    def _start_reader_thread(self):
        raise AssertionError("must not start the reader thread for a rejected file")

    def _start_preload_next(self):
        raise AssertionError("must not preload after a rejected file")


def test_zero_samplerate_is_rejected_without_crashing(tmp_path, monkeypatch):
    fake_path = tmp_path / "damaged.wav"
    fake_path.write_bytes(b"\x00")  # only needs to exist for _resolve_path()

    fake_reader = _FakeReader(samplerate=0)
    monkeypatch.setattr(player_track_loading, "_open_soundfile", lambda sf_module, path: fake_reader)

    player = _Bare()

    assert player.load_track(fake_path) is False
    assert fake_reader.closed is True
    assert player._sf_reader is None
    assert player.error_occurred.emitted, "a rejected file must surface an error to the UI"


def test_negative_samplerate_is_also_rejected(tmp_path, monkeypatch):
    fake_path = tmp_path / "damaged2.wav"
    fake_path.write_bytes(b"\x00")

    fake_reader = _FakeReader(samplerate=-1)
    monkeypatch.setattr(player_track_loading, "_open_soundfile", lambda sf_module, path: fake_reader)

    player = _Bare()

    assert player.load_track(fake_path) is False
    assert fake_reader.closed is True


# ---------------------------------------------------------------------------
# Regression: a corrupt/empty "next" track must not crash the background
# ---------------------------------------------------------------------------


class _BarePreload(PlayerTrackLoadingMixin):
    """Only the state _start_preload_next()/_preload() touch."""

    def __init__(self, next_path: Path):
        self.sf = sf
        self._preload_lock = threading.Lock()
        self._preload_thread = None
        self._preload_generation = 0
        self._next_file = None
        self._next_sf_reader = None
        self._next_sample_rate = 0
        self._next_channels = 0
        self._next_total_frames = 0
        track = SimpleNamespace(track_file_path=str(next_path))
        self.queue_manager = SimpleNamespace(queue=[track, track])  # [current, next]


def test_corrupt_next_track_does_not_crash_preload_thread(tmp_path):
    bad_file = tmp_path / "corrupt.flac"
    bad_file.write_bytes(b"")  # 0 bytes: fails inside SoundFile()'s constructor

    player = _BarePreload(bad_file)

    uncaught = []
    old_hook = threading.excepthook
    threading.excepthook = uncaught.append
    try:
        player._start_preload_next()
        player._preload_thread.join(timeout=5)
    finally:
        threading.excepthook = old_hook

    assert not player._preload_thread.is_alive()
    assert uncaught == [], f"preload thread raised uncaught: {uncaught}"
    assert player._next_sf_reader is None
