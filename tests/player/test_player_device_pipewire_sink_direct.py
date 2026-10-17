"""Regression: a hw node PipeWire holds open must still be offered for bit-perfect output.

PortAudio drops a busy hw: node (e.g. an HDMI receiver on hw:0,3 that is the
desktop's sink) from its device list, so it shows only under its PipeWire sink
name. get_audio_devices() used to label that entry "(Shared)", and
_resolve_exclusive_target() could not map it back to its hw node, so the
receiver could never be picked for bit-perfect playback.

Also: `pactl suspend-sink` reports success even when PipeWire keeps the node
running because a client (e.g. GNOME Settings' level meter on the sink's
monitor) still has a stream on it. _prepare_exclusive_device() then fell back
to the shared sink without telling the user.
"""

import json
import subprocess

from src.player.engine.player_device import PlayerDeviceMixin

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
