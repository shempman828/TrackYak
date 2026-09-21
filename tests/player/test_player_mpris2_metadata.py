"""Regression: MPRIS2 must expose real track metadata and repeat state.

The bug: `_get_props()`'s "Metadata" was always an empty dict and
"LoopStatus" was hardcoded to "None" with `Set()` a no-op, so desktop "now
playing" widgets and shuffle/repeat controls never reflected (or could
control) the player's actual state, even though the app fully registers as
an MPRIS2 media player.
"""

from types import SimpleNamespace

import pytest

dbus = pytest.importorskip("dbus")

from src.player.core.player_mpris2 import MPRIS2_OBJECT_PATH, MPRIS2_PLAYER_IFACE, _MPRIS2DBusService  # noqa: E402


def _service(player):
    svc = _MPRIS2DBusService.__new__(_MPRIS2DBusService)
    svc._player = player
    return svc


def _track(track_id=1, name="Song", artist="Artist", album="Album"):
    return SimpleNamespace(track_id=track_id, track_name=name, primary_artist_names=artist, album_name=album)


def test_metadata_is_empty_when_queue_has_no_current_track():
    player = SimpleNamespace(queue_manager=SimpleNamespace(get_current_track=lambda: None))
    svc = _service(player)
    assert dict(svc._get_metadata()) == {}


def test_metadata_reflects_the_current_track():
    player = SimpleNamespace(queue_manager=SimpleNamespace(get_current_track=lambda: _track()), duration=180_000)
    svc = _service(player)
    metadata = svc._get_metadata()

    assert metadata["mpris:trackid"] == dbus.ObjectPath(f"{MPRIS2_OBJECT_PATH}/Track/1")
    assert metadata["mpris:length"] == 180_000 * 1000
    assert str(metadata["xesam:title"]) == "Song"
    assert list(metadata["xesam:artist"]) == [dbus.String("Artist")]
    assert str(metadata["xesam:album"]) == "Album"


def test_metadata_omits_artist_and_album_when_unavailable():
    track = _track(artist=None, album=None)
    player = SimpleNamespace(queue_manager=SimpleNamespace(get_current_track=lambda: track), duration=0)
    svc = _service(player)
    metadata = svc._get_metadata()

    assert "xesam:artist" not in metadata
    assert "xesam:album" not in metadata


@pytest.mark.parametrize(("repeat_mode", "expected"), [(0, "None"), (1, "Track"), (2, "Playlist")])
def test_loop_status_reflects_repeat_mode(repeat_mode, expected):
    player = SimpleNamespace(queue_manager=SimpleNamespace(get_current_track=lambda: None), volume_level=75, position=0, state="stopped", repeat_mode=repeat_mode)
    svc = _service(player)
    props = svc._get_props(MPRIS2_PLAYER_IFACE)
    assert str(props["LoopStatus"]) == expected


def test_set_loop_status_calls_set_repeat_mode(qapp):
    calls = []
    player = SimpleNamespace(set_repeat_mode=lambda mode: calls.append(mode))
    svc = _service(player)

    svc.Set(MPRIS2_PLAYER_IFACE, "LoopStatus", "Track")
    for _ in range(10):
        qapp.processEvents()
        if calls:
            break

    assert calls == [1]


def test_set_unknown_loop_status_is_ignored(monkeypatch):
    player = SimpleNamespace(set_repeat_mode=lambda mode: pytest.fail("must not be called"))
    svc = _service(player)
    svc.Set(MPRIS2_PLAYER_IFACE, "LoopStatus", "Bogus")
