"""Regression: automatic cyclic GC is suspended while an output stream is live.

A GC collection is stop-the-world — it freezes PortAudio's real-time callback
thread for the whole sweep. A large alloc/free burst elsewhere in the process
(opening a heavy view, an artist merge on a worker thread) trips a gen-2
collection long enough to blow a callback deadline, heard as a playback hitch
even though the audio ring buffer is full. MusicPlayer disables automatic GC
when it opens a stream and re-enables it (with one explicit collect) on close.
"""

import gc

import pytest

from src.player.player_device import PlayerDeviceMixin


class _Bare(PlayerDeviceMixin):
    """PlayerDeviceMixin has no __init__; the gc helpers touch no self state."""


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
