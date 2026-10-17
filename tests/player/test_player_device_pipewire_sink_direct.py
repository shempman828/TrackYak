"""Regression: a hw node PipeWire holds open must still be offered for bit-perfect output.

PortAudio drops a busy hw: node (e.g. an HDMI receiver on hw:0,3 that is the
desktop's sink) from its device list, so it shows only under its PipeWire sink
name. get_audio_devices() used to label that entry "(Shared)", and
_resolve_exclusive_target() could not map it back to its hw node, so the
receiver could never be picked for bit-perfect playback.
"""

import json
import subprocess

from src.player.engine.player_device import PlayerDeviceMixin

SINK = "alsa_output.pci-0000_0a_00.1.hdmi-surround"

_SINKS = [{"name": SINK, "properties": {"alsa.card": "0", "alsa.device": "3"}}, {"name": "bluez_output.headset", "properties": {}}]


class _Bare(PlayerDeviceMixin):
    def __init__(self, devices):
        self.available_devices = devices
        self.current_device = None


def _fake_pactl(monkeypatch):
    def run(cmd, **kwargs):
        assert cmd == ["pactl", "-f", "json", "list", "sinks"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_SINKS))

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
    host._suspended_sink_name = None
    monkeypatch.setattr(host, "_suspend_sink", lambda name, suspend: True)
    monkeypatch.setattr("src.player.engine.player_device.time.sleep", lambda _s: None)

    assert host._prepare_exclusive_device(1) == 0
    assert host.current_device == 0
    assert host._suspended_sink_name == SINK
