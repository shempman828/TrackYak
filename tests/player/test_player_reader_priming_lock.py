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

import soundfile as sf

from src.player import player_reader
from src.player.player_reader import PlayerReaderMixin


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
