"""Tests for PlaylistExporter file content: track order, paths and file name."""

from pathlib import Path
from types import SimpleNamespace

import src.playlist.playlist_export as module
from src.playlist.playlist_export import PlaylistExporter, safe_playlist_filename


def _track(track_id, path):
    return SimpleNamespace(track_id=track_id, track_name=f"T{track_id}", track_file_path=path, track_duration=60, artists=[])


class _FakeController:
    def __init__(self, playlist, playlist_tracks):
        self.get = SimpleNamespace(get_entity_object=lambda entity, **kwargs: playlist, get_all_entities=lambda entity, **kwargs: playlist_tracks)


def test_export_writes_tracks_in_position_order_with_usable_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "playlist_path", lambda name: str(tmp_path / "playlists" / name))
    inside = tmp_path / "playlists" / "music" / "inside.mp3"
    outside = tmp_path / "elsewhere" / "outside.mp3"
    playlist = SimpleNamespace(playlist_name="AC/DC: Best?", playlist_description=None)
    rows = [SimpleNamespace(track_id=2, position=2, track=_track(2, str(outside))), SimpleNamespace(track_id=1, position=1, track=_track(1, str(inside)))]

    assert PlaylistExporter(_FakeController(playlist, rows), show_messages=False).export_playlist(1) is True

    written = tmp_path / "playlists" / "AC_DC_ Best_.m3u"
    entries = [line for line in written.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert entries == [str(Path("music/inside.mp3")), str(outside.resolve())]


def test_safe_playlist_filename_never_returns_an_empty_name():
    assert safe_playlist_filename("...") == "playlist.m3u"
