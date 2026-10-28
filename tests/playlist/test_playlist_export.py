"""PlaylistExporter: file content (order, paths, name) and user messages."""

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox
import pytest

import src.playlist.playlist_export as module
from src.playlist.playlist_export import PlaylistExporter, safe_playlist_filename

# ---------------------------------------------------------------------------
# Tests for PlaylistExporter file content: track order, paths and file name.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression test: PlaylistExporter used QMessageBox.information for every
# ---------------------------------------------------------------------------


class _FakeControllerMessages:
    def __init__(self):
        self.get = SimpleNamespace(get_entity_object=lambda entity, **kwargs: None)


def test_missing_playlist_shows_a_critical_message(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: calls.append(a))
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a: pytest.fail("used .information for an error")
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a: pytest.fail("used .warning for a hard error")
    )
    exporter = PlaylistExporter(_FakeControllerMessages())

    result = exporter.export_playlist(playlist_id=1)

    assert result is False
    assert calls  # QMessageBox.critical was called
