"""Output stream lifecycle: close/teardown race, block size, GC suspension
while live, reader priming lock, and pause/resume finish state.
"""

import collections
import gc
from pathlib import Path
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from src.player.engine import player_feeder, player_reader
from src.player.engine.player_device import PlayerDeviceMixin
from src.player.engine.player_feeder import PlayerFeederMixin
from src.player.engine.player_reader import BLOCKSIZE, PlayerReaderMixin
from src.player.engine.player_realtime import PlayerRealtimeMixin
from src.player.engine.player_transport import STREAM_BLOCKSIZE, PlayerTransportMixin

# ---------------------------------------------------------------------------
# Regression test: _close_stream() must not tear down a live PortAudio
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression: the PortAudio output stream must not be opened with a large
# ---------------------------------------------------------------------------


def test_stream_blocksize_lets_portaudio_choose():
    assert STREAM_BLOCKSIZE == 0


def test_stream_blocksize_is_decoupled_from_reader_decode_chunk():
    # The reader's decode chunk can stay large for efficient disk reads; that
    # must not dictate the real-time callback block.
    assert STREAM_BLOCKSIZE != BLOCKSIZE


# ---------------------------------------------------------------------------
# Regression: automatic cyclic GC is suspended while an output stream is live.
# ---------------------------------------------------------------------------


class _Bare(PlayerRealtimeMixin):
    """PlayerRealtimeMixin has no __init__; the gc helpers touch no self state."""


@pytest.fixture(autouse=True)
def _restore_gc_state():
    was_enabled = gc.isenabled()
    yield
    if was_enabled:
        gc.enable()
    else:
        gc.disable()


def test_suspend_disables_automatic_gc():
    gc.enable()
    _Bare()._suspend_gc_during_playback()
    assert not gc.isenabled()


def test_resume_re_enables_automatic_gc():
    gc.disable()
    _Bare()._resume_gc()
    assert gc.isenabled()


def test_suspend_then_resume_round_trips():
    player = _Bare()
    gc.enable()
    player._suspend_gc_during_playback()
    player._resume_gc()
    assert gc.isenabled()


def test_resume_is_a_noop_when_gc_already_enabled():
    gc.enable()
    _Bare()._resume_gc()  # must not raise
    assert gc.isenabled()


# ---------------------------------------------------------------------------
# Regression: _start_reader_thread()'s buffer-priming step must not block
# ---------------------------------------------------------------------------


class _BareLock(PlayerReaderMixin):
    """Only the state _start_reader_thread()/_reader_loop() touch."""

    def __init__(self):
        self.sf = sf
        self._resolved_file_path: Path | None = None
        self._sf_reader = None
        self.current_channels = 2
        self._total_frames = 0
        self._current_frame = 0
        self._reader_lock = threading.Lock()
        self._audio_buffer: collections.deque = collections.deque()
        self._buffer_lock = threading.Lock()
        self._buffer_epoch = 0
        self._final_chunk_seen = False
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()


def test_start_reader_thread_does_not_hang_when_lock_is_busy(monkeypatch):
    monkeypatch.setattr(player_reader, "READER_LOCK_TIMEOUT", 0.05)
    player = _BareLock()
    player._reader_lock.acquire()  # simulate a stale reader thread stuck holding it
    try:
        start = time.monotonic()
        player._start_reader_thread()
        elapsed = time.monotonic() - start
    finally:
        player._reader_lock.release()

    assert elapsed < 1.0, f"_start_reader_thread() blocked for {elapsed:.2f}s"
    assert player._reader_thread is not None
    player._reader_stop.set()
    player._reader_thread.join(timeout=2)
    assert not player._reader_thread.is_alive()


class _SlowReader:
    """A reader.read() double that blocks the *background* AudioReader
    thread's first call until released, simulating a reader thread genuinely
    stuck on slow/flaky disk I/O while holding _reader_lock -- the scenario
    READER_LOCK_TIMEOUT/join(timeout=...) exist for, but that the
    priming-lock test above never lets run long enough to actually outlive
    _stop_reader_thread()'s join. Only the AudioReader thread's call stalls
    -- not _start_reader_thread()'s own synchronous priming read, which runs
    on the calling (here: main test) thread."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stalled_once = False

    def read(self, n, dtype="float32", always_2d=True):
        if not self.stalled_once and threading.current_thread().name == "AudioReader":
            self.stalled_once = True
            self.entered.set()
            self.release.wait(timeout=5)
        return np.zeros((0, 2), dtype="float32")


def test_stop_reader_thread_does_not_orphan_a_still_stuck_thread(monkeypatch):
    """Regression for bug 531 (playback very rarely incredibly sped up).

    A reader thread that outlives _stop_reader_thread()'s join() must not be
    forgotten. The old code set self._reader_thread = None unconditionally,
    which blinded _start_reader_thread()'s "already running" guard and also
    let it clear _reader_stop -- the exact flag the still-alive thread needed
    to see to exit. The next start spawned a second AudioReader thread that
    ran concurrently with the first, both decoding the same file and racing
    to append into _audio_buffer out of order.
    """
    monkeypatch.setattr(player_reader, "READER_JOIN_TIMEOUT", 0.1)
    player = _BareLock()
    slow = _SlowReader()
    player._sf_reader = slow

    player._start_reader_thread()
    first_thread = player._reader_thread
    assert slow.entered.wait(timeout=2), "reader thread never reached its read"
    assert player._reader_lock.locked()

    player._stop_reader_thread()  # join times out -- thread is still stuck
    assert first_thread.is_alive(), "test setup: thread should still be stuck"

    player._start_reader_thread()
    second_thread = player._reader_thread

    assert second_thread is first_thread, (
        "a second reader thread was spawned while the first was still alive "
        "and stuck holding _reader_lock -- they will race to decode the same "
        "file and can append chunks into _audio_buffer out of order"
    )

    slow.release.set()
    first_thread.join(timeout=2)
    assert not first_thread.is_alive()


# ---------------------------------------------------------------------------
# Regression: play()'s pause/resume fast-path must clear _finish_pending.
# ---------------------------------------------------------------------------


class _Sig:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Timer:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class _ResumeHost(PlayerTransportMixin):
    """Minimal self for PlayerTransportMixin.play()'s resume fast-path."""

    def wake_feeder(self):  # provided by PlayerFeederMixin on the real class
        self.feeder_woken = True

    def __init__(self):
        self.feeder_woken = False
        self.sd = object()  # non-None: skip backend init
        self.current_file = object()  # non-None: skip queue load
        self._sf_reader = object()  # non-None: skip queue load
        self.audio_stream = object()  # non-None: eligible for resume branch
        self.paused = True  # in the resume branch
        self.playing = False
        self._finish_pending = threading.Event()
        self._finish_pending.set()  # as load_track() leaves it
        self._has_reached_threshold = True
        self._play_count_recorded = True
        self.state_changed = _Sig()
        self._position_timer = _Timer()


def test_resume_after_load_while_paused_clears_finish_pending():
    host = _ResumeHost()

    PlayerTransportMixin.play(host)

    # The resume branch was taken...
    assert host.playing is True
    assert host.paused is False
    assert host._position_timer.started is True
    assert ("playing",) in host.state_changed.emitted
    # ...and the end-of-stream flag is cleared, so end-of-track auto-advance
    # will actually fire for the newly loaded track.
    assert not host._finish_pending.is_set()
