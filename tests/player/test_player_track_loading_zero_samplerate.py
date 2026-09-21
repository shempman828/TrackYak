"""Regression: a damaged header reporting samplerate=0 must not crash load_track().

The bug: `self._duration = int(new_frames / new_sr * 1000)` (and the position
math derived from `current_sample_rate` in player_position.py) divides by the
file's reported sample rate with no zero-guard. A corrupt/malformed header
that reports samplerate=0 raised an uncaught ZeroDivisionError instead of
being rejected like any other unopenable file.
"""

from pathlib import Path
import threading
from types import SimpleNamespace

import soundfile as sf

from src.player.core import player_track_loading
from src.player.core.player_track_loading import PlayerTrackLoadingMixin


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
