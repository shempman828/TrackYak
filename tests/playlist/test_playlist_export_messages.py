"""Regression test: PlaylistExporter used QMessageBox.information for every
notice, including outright errors ("Export Error") and partial-failure
warnings -- inconsistent with the rest of the module, which uses
.critical/.warning for anything that isn't a clean success.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox
import pytest

from src.playlist.playlist_export import PlaylistExporter


class _FakeController:
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
    exporter = PlaylistExporter(_FakeController())

    result = exporter.export_playlist(playlist_id=1)

    assert result is False
    assert calls  # QMessageBox.critical was called
