"""Coverage for the Player Dock's waveform wiring — see docs/specs/waveform-scrubber.md.

Exercises the real PlayerUI methods (bound onto a minimal harness, the same
trick as test_player_dock_lyrics_shortcut.py) without standing up a full
MusicPlayer / audio backend.

AC9   track_changed shows the fallback immediately, schedules one worker, and
      applies only a result whose generation still matches
AC10  update_duration enables/disables + ranges the bar; update_position moves
      the playhead but not mid-drag; _on_seek_released still gates on
      "same track + duration > 0"
"""

from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QWidget
import pytest

from src.player.player_dock import PlayerUI
from src.player.waveform_cache import N_BUCKETS
from src.player.waveform_seekbar import WaveformSeekBar


class _DockHarness(QWidget):
    """Real PlayerUI waveform methods on a stripped-down host."""

    _start_waveform_load = PlayerUI._start_waveform_load
    _on_waveform_ready = PlayerUI._on_waveform_ready
    _on_waveform_failed = PlayerUI._on_waveform_failed
    update_duration = PlayerUI.update_duration
    update_position = PlayerUI.update_position
    _on_seek_pressed = PlayerUI._on_seek_pressed
    _on_seek_released = PlayerUI._on_seek_released
    format_time = staticmethod(PlayerUI.format_time)

    seek_requested = Signal(int)

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.controller = SimpleNamespace(mediaplayer=player)
        self._wf_generation = 0
        self.waveform = WaveformSeekBar()
        self.waveform.setGeometry(0, 0, 400, 40)
        self.position_label = QLabel()


@pytest.fixture
def _no_threadpool(monkeypatch):
    """Capture scheduled workers instead of running them."""
    started = []
    monkeypatch.setattr(
        "src.player.player_dock.QThreadPool",
        SimpleNamespace(globalInstance=lambda: SimpleNamespace(start=started.append)),
    )
    return started


# AC9 -------------------------------------------------------------------------


def test_track_change_shows_fallback_and_schedules_one_worker(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=120_000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h.waveform.set_peaks(np.zeros((N_BUCKETS, 2), dtype=np.int8))  # stale envelope

    h._start_waveform_load(src)

    assert h.waveform._peaks is None  # fallback shown immediately
    assert h._wf_generation == 1
    assert len(_no_threadpool) == 1
    assert _no_threadpool[0].generation == 1
    assert _no_threadpool[0].src == src


def test_stale_worker_result_is_ignored_current_one_applies(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=120_000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h._start_waveform_load(src)

    stale = np.zeros((N_BUCKETS, 2), dtype=np.int8)
    h._on_waveform_ready(0, src, stale)
    assert h.waveform._peaks is None

    fresh = np.zeros((N_BUCKETS, 2), dtype=np.int8)
    fresh[:, 1] = 60
    h._on_waveform_ready(h._wf_generation, src, fresh)
    assert h.waveform._peaks is not None


def test_failed_worker_leaves_fallback_in_place(qapp, tmp_path, _no_threadpool):
    src = tmp_path / "song.flac"
    player = SimpleNamespace(duration=1000, _resolved_file_path=src, current_file=src)
    h = _DockHarness(player)
    h._start_waveform_load(src)

    h._on_waveform_failed(h._wf_generation, src, "boom")  # must not raise
    assert h.waveform._peaks is None


# AC10 ----------------------------------------------------------------------------


def test_update_duration_toggles_and_ranges_the_bar(qapp):
    player = SimpleNamespace(duration=0, position=0, current_file=None)
    h = _DockHarness(player)

    h.update_duration(0)
    assert not h.waveform.isEnabled()

    h.update_duration(60_000)
    assert h.waveform.isEnabled()
    assert h.waveform._duration_ms == 60_000


def test_update_position_moves_playhead_except_while_dragging(qapp):
    player = SimpleNamespace(duration=60_000, position=0, current_file=None)
    h = _DockHarness(player)
    h.update_duration(60_000)

    h.update_position(30_000)
    assert h.waveform._position_ms == 30_000

    h.waveform._dragging = True
    h.update_position(45_000)
    assert h.waveform._position_ms == 30_000


def test_seek_released_gates_on_same_track_and_nonzero_duration(qapp, tmp_path):
    a, b = tmp_path / "a.flac", tmp_path / "b.flac"
    player = SimpleNamespace(duration=60_000, position=0, current_file=a)
    h = _DockHarness(player)
    emitted = []
    h.seek_requested.connect(emitted.append)

    h._on_seek_pressed()
    h._on_seek_released(12_345)
    assert emitted == [12_345]

    # track changed since press → drop it
    player.current_file = b
    h._on_seek_released(9_999)
    assert emitted == [12_345]

    # duration went to zero → drop it
    h._on_seek_pressed()
    player.duration = 0
    h._on_seek_released(1)
    assert emitted == [12_345]
