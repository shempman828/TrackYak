"""Regression: play()'s pause/resume fast-path must clear _finish_pending.

load_track() sets _finish_pending (so the outgoing track's callback can't
double-fire track-finished). If a new track is loaded while the player is
paused and playback is then resumed, play() takes the `self.paused` fast-path
at the top. That branch used to return without clearing _finish_pending, so the
flag stayed set for the entire new track -- _emit_track_finished_once() was
suppressed when it ended, _track_finished never fired, auto-advance never
happened, and the audio callback fed silence (thousands of "buffer underrun x5
since last check (buffer now has 0 chunks)" log lines) until the user manually
hit Next. The other two play() branches already clear the flag; this one must
too.
"""

import threading

from src.player.player_transport import PlayerTransportMixin


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
