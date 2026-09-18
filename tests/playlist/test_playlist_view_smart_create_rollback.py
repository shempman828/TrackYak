"""Regression test: if creating a smart playlist fails partway through
(Playlist row created, then the SmartPlaylist/criteria step raises), the
orphaned Playlist row used to stay behind with is_smart=1 and no
SmartPlaylist record -- visible in the tree but permanently broken.
create_smart_playlist must clean it up on failure.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QMessageBox
import pytest
from sqlalchemy.exc import SQLAlchemyError

import src.playlist.playlist_view as playlist_view_module
from src.playlist.playlist_view import PlaylistView


class _FakeSmartCreateDialog:
    """Stands in for SmartPlaylistCreateDialog -- returns canned data
    without ever showing a real (blocking) dialog."""

    def __init__(self, parent=None):
        pass

    def exec_(self):
        return QDialog.Accepted

    def get_data(self):
        criteria = [{"field": "track_name", "comparison": "eq", "value": "x", "type": "String"}]
        return "My Smart Playlist", "desc", "AND", criteria, False


class _FakeController:
    def __init__(self, fail_on_smart_playlist=True):
        self._fail_on_smart_playlist = fail_on_smart_playlist
        self._next_id = 1
        self.created_playlists = {}
        self.deleted = []
        self.get = SimpleNamespace(
            get_all_entities=lambda entity, **kwargs: [],
            get_entity_object=lambda entity, **kwargs: None,
        )
        self.add = SimpleNamespace(add_entity=self._add_entity)
        self.update = SimpleNamespace()
        self.delete = SimpleNamespace(delete_entity=self._delete_entity)

    def _add_entity(self, entity, **kwargs):
        if entity == "Playlist":
            playlist_id = self._next_id
            self._next_id += 1
            obj = SimpleNamespace(playlist_id=playlist_id, **kwargs)
            self.created_playlists[playlist_id] = obj
            return obj
        if entity == "SmartPlaylist" and self._fail_on_smart_playlist:
            raise SQLAlchemyError("boom")
        return SimpleNamespace(playlist_id=kwargs.get("playlist_id"))

    def _delete_entity(self, entity, entity_id):
        self.deleted.append((entity, entity_id))
        if entity == "Playlist":
            self.created_playlists.pop(entity_id, None)
        return True


@pytest.fixture
def view(qapp, monkeypatch):
    monkeypatch.setattr(playlist_view_module, "SmartPlaylistCreateDialog", _FakeSmartCreateDialog)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    v = PlaylistView(_FakeController())
    yield v
    v.deleteLater()


def test_failed_smart_playlist_creation_removes_orphan_playlist(view):
    view.create_smart_playlist()

    assert view.controller.created_playlists == {}
    assert ("Playlist", 1) in view.controller.deleted
