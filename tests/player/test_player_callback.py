"""Regression: _audio_callback must serve the reader's fixed 16384-frame decode
chunks across many smaller, variably-sized PortAudio callback blocks.

STREAM_BLOCKSIZE is 0 (PortAudio picks a small block, see
test_player_stream_blocksize.py) while the reader thread still pushes fixed
BLOCKSIZE (16384) chunks. The callback used to do ``outdata[: len(chunk)] = chunk``
which raised ``could not broadcast input array from shape (16384,2) into shape
(383,2)`` whenever ``frames < len(chunk)`` — swallowed by the realtime-thread
except, dropping the whole chunk to silence.
"""

import collections
import threading

import numpy as np
import pytest

from src.player.player_callback import PlayerCallbackMixin
from src.player.player_reader import BLOCKSIZE

CH = 2


class _FakeEq:
    def process_audio(self, x):  # identity -> output stays byte-comparable
        return x


class _Host(PlayerCallbackMixin):
    def __init__(self, total_frames=0):
        self._buffer_lock = threading.Lock()
        self._audio_buffer = collections.deque()
        self._callback_residual = None
        self._callback_residual_pos = 0
        self._callback_residual_epoch = 0
        self._buffer_epoch = 0
        self._callback_final_chunk_seen = False
        self._stream_generation = 1
        self.playing = True
        self.paused = False
        self._gain_factor = 1.0
        self.volume_level = 100
        self.equalizer = _FakeEq()
        self._total_frames = total_frames
        self._current_frame = 0
        self._frames_played = 0
        self._is_advancing = False
        self._finish_pending = threading.Event()
        self._pending_status_count = 0
        self._last_status_value = None
        self._pending_error_count = 0
        self._last_error_message = None
        self._pending_buffer_underrun_count = 0
        self.finished_emits = 0

        outer = self

        class _Sig:
            def emit(self_):
                outer.finished_emits += 1

        self._track_finished = _Sig()

    def pull(self, frames):
        out = np.full((frames, CH), 99.0, dtype="float32")
        self._audio_callback(out, frames, None, None, 1)
        return out


def _ramp(start, n):
    return np.arange(start * CH, (start + n) * CH, dtype="float32").reshape(n, CH)


def test_small_block_does_not_drop_or_error_a_full_decode_chunk():
    h = _Host()
    chunk = _ramp(0, BLOCKSIZE)
    h._audio_buffer.append(chunk.copy())

    out = h.pull(383)  # << BLOCKSIZE — the shape that used to raise

    assert h._pending_error_count == 0, h._last_error_message
    assert np.array_equal(out, chunk[:383])
    assert h._frames_played == 383


def test_decode_chunk_served_contiguously_across_many_small_blocks():
    h = _Host()
    chunk = _ramp(0, BLOCKSIZE)
    h._audio_buffer.append(chunk.copy())

    got = []
    while h._frames_played < BLOCKSIZE:
        got.append(h.pull(383))
    stitched = np.concatenate(got)[:BLOCKSIZE]

    assert h._pending_error_count == 0
    assert np.array_equal(stitched, chunk)  # no gap, no duplication


def test_callback_block_larger_than_decode_chunk_spans_multiple_chunks():
    h = _Host()
    h._audio_buffer.append(_ramp(0, BLOCKSIZE))
    h._audio_buffer.append(_ramp(BLOCKSIZE, BLOCKSIZE))

    out = h.pull(20000)  # spans two 16384 chunks

    assert h._pending_error_count == 0, h._last_error_message
    assert np.array_equal(out, _ramp(0, 20000))
    assert h._frames_played == 20000


def test_short_final_chunk_zero_fills_tail_and_emits_finished_once():
    h = _Host()  # unknown length: short chunk is the only EOF signal
    h._audio_buffer.append(_ramp(0, 500))  # < BLOCKSIZE == reader's EOF read

    first = h.pull(383)
    assert np.array_equal(first, _ramp(0, 383))
    assert h.finished_emits == 0

    second = h.pull(383)
    assert np.array_equal(second[:117], _ramp(383, 117))
    assert np.all(second[117:] == 0)
    assert h.finished_emits == 1

    h.pull(383)  # extra callbacks must not re-emit
    assert h.finished_emits == 1


def test_zero_length_sentinel_chunk_finishes_without_infinite_loop():
    h = _Host()
    h._audio_buffer.append(np.zeros((0, CH), dtype="float32"))

    out = h.pull(383)

    assert np.all(out == 0)
    assert h.finished_emits == 1
    assert h._pending_error_count == 0


def test_underrun_is_silence_not_finished():
    h = _Host(total_frames=10_000)  # not yet at EOF
    out = h.pull(383)

    assert np.all(out == 0)
    assert h._pending_buffer_underrun_count == 1
    assert h.finished_emits == 0


def test_known_length_eof_emits_finished_when_buffer_and_residual_drain():
    h = _Host(total_frames=BLOCKSIZE)
    h._audio_buffer.append(_ramp(0, BLOCKSIZE))
    h._current_frame = BLOCKSIZE  # reader has read the whole file

    drained = 0
    while drained < BLOCKSIZE:
        h.pull(1000)
        drained += 1000

    assert h.finished_emits == 1
    assert h._pending_error_count == 0


class _ResetOnFirstWrite(np.ndarray):
    """outdata whose first __setitem__ runs `on_write()` — used to land a
    seek/stop reset in the middle of the callback's serve loop, between the
    `_callback_residual_pos` read and its write-back."""

    def __new__(cls, frames, on_write):
        obj = np.full((frames, CH), 99.0, dtype="float32").view(cls)
        obj._on_write = on_write
        obj._fired = False
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self._on_write = getattr(obj, "_on_write", None)
        self._fired = getattr(obj, "_fired", True)

    def __setitem__(self, key, value):
        if not self._fired and self._on_write is not None:
            self._fired = True
            self._on_write()
        super().__setitem__(key, value)


def _reset_buffer(h):
    """What _stop_reader_thread / stop() / seek() do to the ring buffer."""
    with h._buffer_lock:
        h._audio_buffer.clear()
        h._buffer_epoch += 1
        h._callback_final_chunk_seen = False


def test_reset_mid_serve_loop_does_not_tear_residual_state():
    """A seek/stop landing between the residual-pos read and its write-back
    used to leave `_callback_residual is None` with a non-zero
    `_callback_residual_pos`, so the next drained-check did `len(None)` and
    the swallowed TypeError blanked a whole callback block. The epoch guard
    means reset no longer nulls the residual, so no tear and no error."""
    h = _Host()
    h._audio_buffer.append(_ramp(0, 1000))

    h.pull(10)  # install residual, advance pos to 10
    assert h._callback_residual is not None
    assert h._callback_residual_pos == 10

    out = _ResetOnFirstWrite(10, lambda: _reset_buffer(h))
    h._audio_callback(out, 10, None, None, 1)

    assert h._pending_error_count == 0, h._last_error_message
    assert not (h._callback_residual is None and h._callback_residual_pos != 0)

    # Next callback: buffer was cleared and the epoch moved, so the stale
    # residual must be discarded — clean underrun silence, not stale audio.
    nxt = h.pull(10)
    assert h._pending_error_count == 0
    assert h._pending_buffer_underrun_count == 1
    assert np.all(nxt == 0)


def test_reset_during_chunk_eq_discards_the_stale_chunk():
    """If a reset fires while the callback is gain/EQ-ing a freshly popped
    chunk, that chunk belongs to the pre-reset buffer and must not be served
    or installed as the residual."""
    h = _Host()
    h._audio_buffer.append(_ramp(0, BLOCKSIZE))

    fired = []

    class _ResettingEq:
        def process_audio(self, x):
            if not fired:
                fired.append(True)
                _reset_buffer(h)
            return x

    h.equalizer = _ResettingEq()

    out = h.pull(383)

    assert fired == [True]
    assert h._pending_error_count == 0, h._last_error_message
    assert np.all(out == 0)  # stale chunk not served
    assert h._callback_residual is None  # stale chunk not installed
    assert h._frames_played == 0


@pytest.mark.parametrize("gain", [0.5, 2.0])
def test_gain_applied_once_per_chunk_not_per_block(gain):
    h = _Host()
    h._gain_factor = gain
    chunk = _ramp(0, BLOCKSIZE)
    h._audio_buffer.append(chunk.copy())

    got = []
    while h._frames_played < BLOCKSIZE:
        got.append(h.pull(500))
    stitched = np.concatenate(got)[:BLOCKSIZE]

    assert np.allclose(stitched, chunk * gain)
