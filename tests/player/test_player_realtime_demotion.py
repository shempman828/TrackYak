"""Regression: exclusive-mode real-time promotion must be undone, and the
promote worker must never latch onto a stale/foreign thread id.

RTKit promotes the audio feeder thread to SCHED_RR for bit-perfect output.
There used to be no counterpart demotion, and the promote worker acted on
whatever ``_feeder_native_tid`` happened to hold -- which is stale on the
2nd+ exclusive stream of a session (it is never cleared) and can be a
recycled tid belonging to an unrelated, long-lived thread. Toggling
exclusive mode off then left a real-time thread running on the normal
PipeWire path, with an ``RLIMIT_RTTIME`` -> SIGXCPU/SIGKILL hazard if it
ever spun.
"""

import os
import threading
import time

import pytest

from src.player import player_device
from src.player.player_device import PlayerDeviceMixin, demote_thread_from_realtime

_LINUX_SCHED = hasattr(os, "sched_setscheduler")


# ── demote_thread_from_realtime ─────────────────────────────────────────────


@pytest.mark.skipif(not _LINUX_SCHED, reason="sched_setscheduler is Linux-only")
def test_demote_leaves_a_live_thread_on_the_normal_scheduler():
    done = threading.Event()
    box = {}

    def _t():
        box["ok"] = demote_thread_from_realtime(threading.get_native_id())
        box["policy"] = os.sched_getscheduler(0)
        done.set()

    threading.Thread(target=_t, daemon=True).start()
    assert done.wait(2.0)
    assert box["ok"] is True
    assert box["policy"] == os.SCHED_OTHER


@pytest.mark.skipif(not _LINUX_SCHED, reason="sched_setscheduler is Linux-only")
def test_demote_is_a_silent_noop_for_a_dead_or_unknown_tid():
    # A tid that cannot belong to a live thread in this process: no raise.
    assert demote_thread_from_realtime(0x7FFFFFFF) is False


def test_demote_is_a_noop_without_sched_setscheduler(monkeypatch):
    monkeypatch.delattr(os, "sched_setscheduler", raising=False)
    assert demote_thread_from_realtime(threading.get_native_id()) is False


# ── _request_exclusive_realtime_priority worker ────────────────────────────


class _DeviceHost(PlayerDeviceMixin):
    def __init__(self, *, stream_generation, feeder_generation, feeder_tid, exclusive):
        self._stream_generation = stream_generation
        self._feeder_generation = feeder_generation
        self._feeder_native_tid = feeder_tid
        self.exclusive_mode = exclusive
        self.promoted: list[tuple[int, int]] = []

    def _promote_callback_to_realtime(self, tid, priority):
        self.promoted.append((tid, priority))
        return True


def _run_promotion(host, monkeypatch, poll_timeout=0.15):
    monkeypatch.setattr(player_device, "REALTIME_PROMOTION_POLL_TIMEOUT", poll_timeout)
    host._request_exclusive_realtime_priority()
    time.sleep(poll_timeout + 0.3)
    for t in threading.enumerate():
        if t.name == "RTKitPromote":
            t.join(timeout=2.0)


def test_worker_ignores_a_stale_feeder_tid_from_a_previous_stream(monkeypatch):
    # 2nd exclusive stream: generation moved on, but the feeder for it has
    # not recorded its tid yet -- _feeder_generation/_feeder_native_tid still
    # describe the previous (dead, maybe tid-recycled) feeder.
    host = _DeviceHost(stream_generation=7, feeder_generation=6, feeder_tid=999999, exclusive=True)
    _run_promotion(host, monkeypatch)
    assert host.promoted == []


def test_worker_promotes_the_feeder_started_for_this_stream(monkeypatch):
    host = _DeviceHost(stream_generation=7, feeder_generation=7, feeder_tid=4242, exclusive=True)
    _run_promotion(host, monkeypatch)
    assert host.promoted == [(4242, player_device.REALTIME_PROMOTION_PRIORITY)]


def test_worker_bails_if_exclusive_mode_turned_off_while_waiting(monkeypatch):
    host = _DeviceHost(stream_generation=7, feeder_generation=6, feeder_tid=None, exclusive=True)

    def _feeder_comes_up_after_toggle_off():
        time.sleep(0.05)
        host.exclusive_mode = False  # user toggled it off first
        host._feeder_generation = 7
        host._feeder_native_tid = 4242

    threading.Thread(target=_feeder_comes_up_after_toggle_off, daemon=True).start()
    _run_promotion(host, monkeypatch, poll_timeout=0.3)
    assert host.promoted == []


def test_worker_bails_if_the_stream_turned_over_while_waiting(monkeypatch):
    host = _DeviceHost(stream_generation=7, feeder_generation=6, feeder_tid=None, exclusive=True)

    def _stream_replaced_before_feeder_registered():
        time.sleep(0.05)
        host._stream_generation = 8  # stop()/play() already opened the next stream
        host._feeder_generation = 7
        host._feeder_native_tid = 4242

    threading.Thread(target=_stream_replaced_before_feeder_registered, daemon=True).start()
    _run_promotion(host, monkeypatch, poll_timeout=0.3)
    assert host.promoted == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
