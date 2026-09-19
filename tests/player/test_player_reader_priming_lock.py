"""Regression: _start_reader_thread()'s buffer-priming step must not block
the calling (usually UI) thread forever when _reader_lock is already held.

The bug: the priming block took `_reader_lock` with a bare `with`, while
every other acquisition of this lock in the codebase uses a bounded
`.acquire(timeout=READER_LOCK_TIMEOUT)`. _stop_reader_thread()'s
join(timeout=2.0) gives up on -- but does not kill -- a reader thread stuck
on a slow read, so that stale thread can still be holding _reader_lock when
_start_reader_thread() (called right after, by load_track()/play()/seek())
reaches the bare `with`. That's the "Python is not responding" failure mode
READER_LOCK_TIMEOUT exists to prevent everywhere else.
"""

import collections
from pathlib import Path
import threading
import time

import numpy as np
import soundfile as sf

from src.player.core import player_reader
from src.player.core.player_reader import PlayerReaderMixin


class _Bare(PlayerReaderMixin):
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
    player = _Bare()
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
    player = _Bare()
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
