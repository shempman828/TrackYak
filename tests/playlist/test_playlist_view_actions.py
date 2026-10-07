"""Tests for PlaylistView delete, rename and edit actions."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItemIterator
import pytest

from src.playlist.playlist_view import PlaylistView


def _playlist(playlist_id, name, parent_id=None, is_smart=False):
    return SimpleNamespace(playlist_id=playlist_id, playlist_name=name, parent_id=parent_id, is_smart=is_smart, track_count=0)


class _FakeController:
    def __init__(self, playlists):
        self._by_id = {p.playlist_id: p for p in playlists}
        self.updates = []
        self.deleted = []
        self.get = SimpleNamespace(
            get_all_entities=lambda entity, **kwargs: list(self._by_id.values()) if entity == "Playlist" else [],
            get_entity_object=lambda entity, playlist_id=None, **kwargs: self._by_id.get(playlist_id),
        )
        self.update = SimpleNamespace(update_entity=self._update_entity)
        self.delete = SimpleNamespace(delete_entity=self._delete_entity)
        self.add = SimpleNamespace()

    def _update_entity(self, entity, entity_id, **fields):
        self.updates.append((entity, entity_id, fields))
        for key, value in fields.items():
            setattr(self._by_id[entity_id], key, value)
        return True

    def _delete_entity(self, entity, entity_id):
        self.deleted.append(entity_id)
        self._by_id.pop(entity_id, None)
        return True


def _find_item(tree, playlist_id):
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        data = it.value().data(0, Qt.UserRole)
        if data and data[1] == playlist_id:
            return it.value()
        it += 1
    return None


@pytest.fixture
def view(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    playlists = [_playlist(1, "alpha"), _playlist(2, "beta"), _playlist(3, "gamma"), _playlist(4, "🔍 Search", is_smart=False), _playlist(5, "smarty", is_smart=True)]
    v = PlaylistView(_FakeController(playlists))
    yield v
    v.deleteLater()


def test_delete_removes_every_selected_playlist(view):
    view.tree.setCurrentItem(_find_item(view.tree, 1))
    _find_item(view.tree, 2).setSelected(True)

    view.delete_selected()

    assert sorted(view.controller.deleted) == [1, 2]
    assert _find_item(view.tree, 3) is not None


def test_delete_closes_the_open_track_window(view):
    closed = []
    view.open_playlist_windows[3] = SimpleNamespace(close=lambda: closed.append(3))
    view.tree.setCurrentItem(_find_item(view.tree, 3))

    view.delete_selected()

    assert closed == [3]
    assert 3 not in view.open_playlist_windows


def test_rename_keeps_a_leading_marker_on_a_normal_playlist(view):
    item = _find_item(view.tree, 4)
    item.setText(0, "🔍 Search Results")

    assert view.controller.updates[-1] == ("Playlist", 4, {"playlist_name": "🔍 Search Results"})


def test_rename_strips_the_marker_on_a_smart_playlist(view):
    item = _find_item(view.tree, 5)
    item.setText(0, "🔍 renamed")

    assert view.controller.updates[-1] == ("Playlist", 5, {"playlist_name": "renamed"})


def test_unchanged_rename_does_not_write(view):
    item = _find_item(view.tree, 5)
    item.setText(0, "🔍 smarty ")

    assert view.controller.updates == []


def test_edit_playlist_ignores_a_row_without_playlist_data(view):
    view.tree.clear()
    view._add_empty_state_item()
    view.tree.setCurrentItem(view.tree.topLevelItem(0))

    view.edit_playlist()  # must not raise
