"""Regression test for the exclusive-mode RTKit priority regression.

REALTIME_PROMOTION_PRIORITY=10 left the promoted audio callback thread below
several SCHED_FIFO-50 kernel threads (DRM display CRTC vblank workers,
some IRQ threads), so any display activity during exclusive-mode playback
could still preempt it and cause a real PortAudio output underflow -- the
original RTKit fix (bug #? "exclusive-mode glitches on background activity")
only closed part of the gap. 20 is the maximum RTKit's own
MaxRealtimePriority ceiling will grant on this system.
"""
from src.player.player_device import REALTIME_PROMOTION_PRIORITY

RTKIT_MAX_REALTIME_PRIORITY = 20


def test_requests_the_maximum_priority_rtkit_will_grant():
    assert REALTIME_PROMOTION_PRIORITY == RTKIT_MAX_REALTIME_PRIORITY


def test_priority_exceeds_ordinary_scheduling():
    # SCHED_OTHER (the default for every non-realtime thread, including
    # whatever CPU-heavy background work triggers the hitch) always loses to
    # any SCHED_RR/FIFO thread regardless of the RR priority number, but a
    # priority of 0 or below would be rejected by RTKit outright.
    assert 0 < REALTIME_PROMOTION_PRIORITY <= RTKIT_MAX_REALTIME_PRIORITY
