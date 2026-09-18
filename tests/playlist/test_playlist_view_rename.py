"""Regression test: renaming a playlist in the tree (double-click/F2) used
to change the displayed text but never save it -- the item was flagged
Qt.ItemIsEditable with no itemChanged handler, so the edit was silently
discarded on the next reload.
"""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItemIterator
import pytest

from src.playlist.playlist_view import PlaylistView


class _FakePlaylist(SimpleNamespace):
    pass


def _playlists():
    return [
        _FakePlaylist(
            playlist_id=1, playlist_name="Old Name", parent_id=None, is_smart=False, track_count=0
        )
    ]


class _FakeController:
    def __init__(self, playlists):
        self._by_id = {p.playlist_id: p for p in playlists}
        self.updates = []
        self.get = SimpleNamespace(
            get_all_entities=self._get_all_entities,
            get_entity_object=lambda entity, playlist_id: self._by_id.get(playlist_id),
        )
        self.update = SimpleNamespace(update_entity=self._update_entity)
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()

    def _get_all_entities(self, entity, **kwargs):
        if entity != "Playlist":
            return []
        return list(self._by_id.values())

    def _update_entity(self, entity, entity_id, **fields):
        self.updates.append((entity, entity_id, fields))
        if entity == "Playlist" and "playlist_name" in fields:
            self._by_id[entity_id].playlist_name = fields["playlist_name"]
        return True


def _find_item(tree, playlist_id):
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        item = it.value()
        data = item.data(0, Qt.UserRole)
        if data and data[1] == playlist_id:
            return item
        it += 1
    return None


@pytest.fixture
def view(qapp):
    v = PlaylistView(_FakeController(_playlists()))
    yield v
    v.deleteLater()


def test_renaming_item_persists_to_database(view):
    item = _find_item(view.tree, 1)

    item.setText(0, "New Name")

    assert ("Playlist", 1, {"playlist_name": "New Name"}) in view.controller.updates
    assert view.controller._by_id[1].playlist_name == "New Name"


def test_renaming_to_blank_reverts_and_does_not_save(view):
    item = _find_item(view.tree, 1)

    item.setText(0, "   ")

    assert not any(u[0] == "Playlist" and "playlist_name" in u[2] for u in view.controller.updates)
    assert item.text(0) == "Old Name"
