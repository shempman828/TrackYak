"""Output device selection: PipeWire-held hw nodes for bit-perfect output,
and exclusive-mode real-time priority promotion and demotion.
"""

import json
import os
import subprocess
import threading
import time

import pytest

from src.player.engine import player_realtime
from src.player.engine.player_device import PlayerDeviceMixin
from src.player.engine.player_realtime import REALTIME_PROMOTION_PRIORITY, PlayerRealtimeMixin, demote_thread_from_realtime

# ---------------------------------------------------------------------------
# Regression: a hw node PipeWire holds open must still be offered for bit-perfect output.
# ---------------------------------------------------------------------------


SINK = "alsa_output.pci-0000_0a_00.1.hdmi-surround"


_PACTL = {
    "sinks": [
        {"index": 3003, "name": SINK, "monitor_source": SINK + ".monitor", "properties": {"alsa.card": "0", "alsa.device": "3"}},
        {"index": 70, "name": "bluez_output.headset", "properties": {}},
    ],
    "sources": [{"index": 3004, "name": SINK + ".monitor"}],
    "sink-inputs": [],
    "source-outputs": [{"source": 3004, "properties": {"application.name": "GNOME Settings"}}],
}


class _Bare(PlayerDeviceMixin):
    def __init__(self, devices):
        self.available_devices = devices
        self.current_device = None
        self._suspended_sink_name = None
        self.errors = []
        self.error_occurred = type("_Signal", (), {"emit": staticmethod(self.errors.append)})()


def _fake_pactl(monkeypatch):
    def run(cmd, **kwargs):
        assert cmd[:4] == ["pactl", "-f", "json", "list"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_PACTL[cmd[4]]))

    monkeypatch.setattr(subprocess, "run", run)


def _dev(name, channels=2):
    return {"name": name, "max_output_channels": channels}


def test_alsa_backed_sink_is_listed_as_direct(monkeypatch):
    _fake_pactl(monkeypatch)
    host = _Bare([_dev("HDA ATI HDMI: 1 (hw:0,7)"), _dev("pipewire"), _dev(SINK, 6), _dev("bluez_output.headset")])
    direct = {d["name"]: d["direct"] for d in host.get_audio_devices()}
    assert direct == {"HDA ATI HDMI: 1 (hw:0,7)": True, "pipewire": False, SINK: True, "bluez_output.headset": False}


def test_sink_name_resolves_to_its_hw_node(monkeypatch):
    _fake_pactl(monkeypatch)
    host = _Bare([_dev("pipewire"), _dev(SINK, 6)])
    assert host._resolve_exclusive_target(1) == ((0, 3), SINK)


def test_sink_without_alsa_node_does_not_resolve(monkeypatch):
    _fake_pactl(monkeypatch)
    host = _Bare([_dev("bluez_output.headset")])
    assert host._resolve_exclusive_target(0) == (None, None)


def test_grabbed_hw_node_replaces_stale_current_device(monkeypatch):
    _fake_pactl(monkeypatch)

    class _FakeSd:
        def _terminate(self):
            pass

        def _initialize(self):
            pass

        def query_devices(self):
            # After the sink is suspended, the freed hw node appears and
            # every index shifts.
            return [_dev("HDA ATI HDMI: 0 (hw:0,3)", 8), _dev("pipewire")]

    host = _Bare([_dev("pipewire"), _dev(SINK, 6)])
    host.sd = _FakeSd()
    host.current_device = 1
    monkeypatch.setattr(host, "_suspend_sink", lambda name, suspend: True)
    monkeypatch.setattr(host, "_hw_node_is_open", lambda hw_ids: False)
    monkeypatch.setattr("src.player.engine.player_device.time.sleep", lambda _s: None)

    assert host._prepare_exclusive_device(1) == 0
    assert host.current_device == 0
    assert host._suspended_sink_name == SINK
    assert host.errors == []


def test_hw_node_still_held_after_suspend_resumes_sink_and_names_the_holder(monkeypatch):
    _fake_pactl(monkeypatch)
    calls = []

    class _NoRescanSd:
        def _terminate(self):
            raise AssertionError("must not rescan while the hw node is still held")

    host = _Bare([_dev("pipewire"), _dev(SINK, 6)])
    host.sd = _NoRescanSd()
    monkeypatch.setattr(host, "_suspend_sink", lambda name, suspend: calls.append((name, suspend)) or True)
    monkeypatch.setattr(host, "_hw_node_is_open", lambda hw_ids: True)
    monkeypatch.setattr("src.player.engine.player_device.time.sleep", lambda _s: None)

    assert host._prepare_exclusive_device(1) is None
    assert calls == [(SINK, True), (SINK, False)]
    assert host._suspended_sink_name is None
    assert len(host.errors) == 1
    assert "GNOME Settings is using hw:0,3" in host.errors[0]


def test_hw_node_status_reads_proc_asound(monkeypatch, tmp_path):
    sub = tmp_path / "card0" / "pcm3p" / "sub0"
    sub.mkdir(parents=True)
    status = sub / "status"
    real_path = PlayerDeviceMixin._hw_node_is_open.__globals__["Path"]
    monkeypatch.setattr("src.player.engine.player_device.Path", lambda p: real_path(str(p).replace("/proc/asound", str(tmp_path))))

    status.write_text("state: RUNNING\nowner_pid   : 1538723\n")
    assert PlayerDeviceMixin._hw_node_is_open((0, 3)) is True
    status.write_text("closed\n")
    assert PlayerDeviceMixin._hw_node_is_open((0, 3)) is False


# ---------------------------------------------------------------------------
# Regression test for the exclusive-mode RTKit priority regression.
# ---------------------------------------------------------------------------


RTKIT_MAX_REALTIME_PRIORITY = 20


def test_requests_the_maximum_priority_rtkit_will_grant():
    assert REALTIME_PROMOTION_PRIORITY == RTKIT_MAX_REALTIME_PRIORITY


def test_priority_exceeds_ordinary_scheduling():
    # SCHED_OTHER (the default for every non-realtime thread, including
    # whatever CPU-heavy background work triggers the hitch) always loses to
    # any SCHED_RR/FIFO thread regardless of the RR priority number, but a
    # priority of 0 or below would be rejected by RTKit outright.
    assert 0 < REALTIME_PROMOTION_PRIORITY <= RTKIT_MAX_REALTIME_PRIORITY


# ---------------------------------------------------------------------------
# Regression: exclusive-mode real-time promotion must be undone, and the
# ---------------------------------------------------------------------------


_LINUX_SCHED = hasattr(os, "sched_setscheduler")


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


class _DeviceHost(PlayerRealtimeMixin):
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
    monkeypatch.setattr(player_realtime, "REALTIME_PROMOTION_POLL_TIMEOUT", poll_timeout)
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
    assert host.promoted == [(4242, player_realtime.REALTIME_PROMOTION_PRIORITY)]


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
