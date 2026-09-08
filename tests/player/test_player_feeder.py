"""Behaviour lock-in for the audio feeder thread (player_feeder.py).

The feeder replaced the Python realtime callback: it pulls the reader's decode
chunks out of the ring buffer, applies gain + EQ, and writes them to the output
stream with blocking stream.write() calls from an ordinary thread — so no
Python runs on PortAudio's realtime thread. Under two permanently GIL-pinned
threads the old callback model produced ~110 output underflows / 12 s; the
feeder model with OUTPUT_LATENCY headroom produces 0-2, and none under
realistic bursty load.
"""

import collections
import threading
import time

import numpy as np
import pytest

from src.player.player_feeder import FEEDER_WRITE_BLOCKSIZE, PlayerFeederMixin
from src.player.player_reader import BLOCKSIZE

CH = 2


class _FakeEq:
    def process_audio(self, x):
        return x  # identity -> output stays comparable


class _FakeStream:
    def __init__(self, underflow_after=None):
        self.active = True
        self.writes: list[np.ndarray] = []
        self.aborted = 0
        # If set, write() reports underflowed=True from the Nth write onward.
        self.underflow_after = underflow_after

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def abort(self):
        self.aborted += 1

    def write(self, data):
        assert data.dtype == np.float32
        assert data.ndim == 2 and data.shape[1] == CH
        assert data.flags["C_CONTIGUOUS"]
        assert len(data) <= FEEDER_WRITE_BLOCKSIZE
        self.writes.append(np.array(data, copy=True))
        return self.underflow_after is not None and len(self.writes) >= self.underflow_after

    def frames_written(self) -> int:
        return sum(len(w) for w in self.writes)

    def stacked(self) -> np.ndarray:
        return np.concatenate(self.writes, axis=0) if self.writes else np.empty((0, CH), np.float32)


class _Host(PlayerFeederMixin):
    def __init__(self, *, total_frames=0):
        self._buffer_lock = threading.Lock()
        self._audio_buffer = collections.deque()
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
        self.audio_stream = _FakeStream()
        self.paused = False
        self._gain_factor = 1.0
        self.volume_level = 100
        self.equalizer = _FakeEq()
        self._total_frames = total_frames
        self._current_frame = 0
        self._frames_played = 0
        self._pending_error_count = 0
        self._last_error_message = None
        self._pending_output_underflow_count = 0
        self._pending_buffer_underrun_count = 0
        self.playing = True

        class _Sig:
            emits = 0
            event = threading.Event()

            def emit(self_):
                _Sig.emits += 1
                _Sig.event.set()

        self._track_finished = _Sig()

    def run_feeder_until(self, predicate, timeout=2.0):
        """Run the feeder loop on a background thread until `predicate()` is
        true (or timeout), then stop it and join."""
        th = threading.Thread(target=self._feeder_loop, args=(1,), daemon=True)
        th.start()
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and not predicate():
                time.sleep(0.005)
            assert predicate(), "feeder did not reach expected state in time"
        finally:
            self._feeder_stop.set()
            self._feeder_wake.set()
            th.join(timeout=2.0)
            assert not th.is_alive()


def _chunk(frames, value=0.5):
    return np.full((frames, CH), value, dtype=np.float32)


def test_feeder_writes_a_chunk_with_gain_applied():
    h = _Host()
    h.volume_level = 50  # -> gain 0.5
    h._audio_buffer.append(_chunk(1000, value=0.4))

    h.run_feeder_until(lambda: h.audio_stream.frames_written() >= 1000)

    assert h.audio_stream.frames_written() == 1000
    assert h._frames_played == 1000
    np.testing.assert_allclose(h.audio_stream.stacked(), 0.2, rtol=1e-6)


def test_feeder_slices_writes_to_stay_responsive():
    h = _Host()
    h._audio_buffer.append(_chunk(BLOCKSIZE))  # one full decode chunk

    h.run_feeder_until(lambda: h.audio_stream.frames_written() >= BLOCKSIZE)

    # A 16384-frame chunk must go out as several small writes, not one.
    assert len(h.audio_stream.writes) >= BLOCKSIZE // FEEDER_WRITE_BLOCKSIZE
    assert h.audio_stream.frames_written() == BLOCKSIZE


def test_feeder_emits_track_finished_once_on_short_final_chunk():
    h = _Host(total_frames=BLOCKSIZE + 100)
    h._audio_buffer.append(_chunk(BLOCKSIZE))
    h._audio_buffer.append(_chunk(100))  # short -> final

    h.run_feeder_until(lambda: h._track_finished.emits >= 1)
    time.sleep(0.05)  # give any erroneous second emit a chance

    assert h._track_finished.emits == 1
    assert h._final_chunk_seen is True
    assert h.audio_stream.frames_written() == BLOCKSIZE + 100


def test_feeder_emits_on_zero_length_eof_sentinel():
    h = _Host(total_frames=0)
    h._audio_buffer.append(_chunk(500))
    h._audio_buffer.append(np.empty((0, CH), dtype=np.float32))

    h.run_feeder_until(lambda: h._track_finished.emits >= 1)

    assert h._track_finished.emits == 1
    assert h.audio_stream.frames_written() == 500


def test_feeder_drops_a_chunk_whose_epoch_went_stale():
    h = _Host()

    class _ResettingEq:
        """Stands in for a seek/stop landing while this chunk is being
        processed: the epoch moves after the feeder captured it."""

        def process_audio(self_, x):
            h._buffer_epoch += 1
            return x

    h.equalizer = _ResettingEq()
    h._audio_buffer.append(_chunk(800))

    h.run_feeder_until(lambda: len(h._audio_buffer) == 0)
    time.sleep(0.05)

    assert h.audio_stream.frames_written() == 0  # stale chunk never written
    assert h._frames_played == 0


def test_feeder_counts_portaudio_output_underflow_from_write_return():
    h = _Host()
    h.audio_stream = _FakeStream(underflow_after=2)  # writes 2..N report underflow
    h._audio_buffer.append(_chunk(BLOCKSIZE))

    h.run_feeder_until(lambda: h.audio_stream.frames_written() >= BLOCKSIZE)

    n_writes = len(h.audio_stream.writes)
    assert h._pending_output_underflow_count == n_writes - 2 + 1


def test_feeder_counts_underrun_only_while_not_finishing():
    h = _Host(total_frames=0)
    h._finish_pending = threading.Event()  # clear: real underrun territory

    h.run_feeder_until(lambda: h._pending_buffer_underrun_count > 0)
    assert h._pending_buffer_underrun_count > 0

    # Once end-of-track is pending, an empty buffer is expected, not a fault.
    h._pending_buffer_underrun_count = 0
    h._finish_pending.set()
    h.run_feeder_until(lambda: True, timeout=0.2)
    assert h._pending_buffer_underrun_count == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
