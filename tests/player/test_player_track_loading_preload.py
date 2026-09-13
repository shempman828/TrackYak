"""Regression: a corrupt/empty "next" track must not crash the background
preload thread.

The bug: `_preload()`'s except clause only caught `OSError`, but
`soundfile.LibsndfileError` subclasses `RuntimeError`, not `OSError` -- so a
file that fails at *open* time (e.g. an empty file, "Format not recognised")
escaped the TrackPreload thread uncaught instead of being logged as a
warning like every other `_open_soundfile()` call site in this codebase.
"""

from pathlib import Path
import threading
from types import SimpleNamespace

import soundfile as sf

from src.player.player_track_loading import PlayerTrackLoadingMixin


class _Bare(PlayerTrackLoadingMixin):
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

    player = _Bare(bad_file)

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
