"""Regression test: _close_stream() must not tear down a live PortAudio
stream while the feeder thread is still stuck inside it.

player_feeder.py's own module docstring states the invariant: "the main
thread only constructs the stream and closes it once the feeder has been
joined." _stop_feeder_thread() correctly leaves a still-stuck feeder thread
in place (see test_player_feeder.py's
test_stop_feeder_thread_does_not_orphan_a_still_stuck_thread), but
_close_stream() used to call stream.stop()/close() unconditionally right
after, regardless of whether the feeder thread actually stopped -- racing a
real, possibly real-time-priority OS thread that may still be calling into
PortAudio. That's undefined behaviour at the native layer and a plausible
hard-crash source, not just a Python-level bug.
"""

import collections
import threading
import time

import numpy as np
import pytest

from src.player.core import player_feeder
from src.player.core.player_device import PlayerDeviceMixin
from src.player.core.player_feeder import PlayerFeederMixin
from src.player.core.player_realtime import PlayerRealtimeMixin

CH = 2


class _FakeEq:
    def process_audio(self, x):
        return x


class _StuckStream:
    """A stream whose write() blocks until released, simulating a wedged
    output device / slow I/O that stream.write() can genuinely hang on."""

    def __init__(self):
        self.active = True
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed_while_writing = False

    def start(self):
        self.active = True

    def stop(self):
        if self.entered.is_set() and not self.release.is_set():
            self.closed_while_writing = True

    def close(self):
        if self.entered.is_set() and not self.release.is_set():
            self.closed_while_writing = True

    def write(self, data):
        self.entered.set()
        self.release.wait(timeout=5)
        return False


class _Host(PlayerDeviceMixin, PlayerRealtimeMixin, PlayerFeederMixin):
    def __init__(self):
        self._buffer_lock = threading.Lock()
        self._audio_buffer = collections.deque([np.zeros((100, CH), dtype="float32")])
        self._buffer_epoch = 0
        self._final_chunk_seen = False
        self._finish_pending = threading.Event()
        self._stream_generation = 1
        self._feeder_generation = 1
        self._feeder_thread = None
        self._feeder_stop = threading.Event()
        self._feeder_wake = threading.Event()
        self._feeder_flush = threading.Event()
        self._feeder_native_tid = None
        self.audio_stream = _StuckStream()
        self.paused = False
        self._gain_factor = 1.0
        self.volume_level = 100
        self.equalizer = _FakeEq()
        self._total_frames = 0
        self._current_frame = 0
        self._frames_played = 0
        self._pending_error_count = 0
        self._last_error_message = None
        self._pending_output_underflow_count = 0
        self._pending_buffer_underrun_count = 0
        self.playing = True
        self._suspended_sink_name = None
        self.sd = None

        class _Sig:
            def emit(self_):
                pass

        self._track_finished = _Sig()


def test_close_stream_does_not_race_a_still_stuck_feeder(monkeypatch):
    monkeypatch.setattr(player_feeder, "FEEDER_JOIN_TIMEOUT", 0.1)
    h = _Host()
    stream = h.audio_stream

    h._start_feeder_thread()
    assert stream.entered.wait(timeout=2), "feeder thread never reached its write()"

    closed = h._close_stream()

    assert closed is False, "_close_stream() must report failure when the feeder is stuck"
    assert h._feeder_thread is not None and h._feeder_thread.is_alive(), "stuck feeder thread must be left in place, not orphaned"
    assert h.audio_stream is stream, "_close_stream() must leave self.audio_stream alone while the feeder that owns it is still alive"
    assert not stream.closed_while_writing, "_close_stream() called stop()/close() on the stream while the feeder thread was still inside write() on it -- a native-level race"

    # Let the feeder unstick and exit; its own teardown stops the stream.
    stream.release.set()
    h._feeder_stop.set()
    h._feeder_wake.set()
    h._feeder_thread.join(timeout=2)
    assert not h._feeder_thread.is_alive()

    # Now a real close should succeed and fully tear things down.
    time.sleep(0.05)
    assert h._close_stream() is True
    assert h.audio_stream is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
