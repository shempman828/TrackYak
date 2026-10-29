"""PlaylistView: delete/rename/edit actions, in-tree rename, drag-and-drop
reparenting, depth cap, empty state, and smart-create rollback.
"""

import logging
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QTreeWidgetItemIterator
import pytest
from sqlalchemy.exc import SQLAlchemyError

import src.playlist.playlist_view as playlist_view_module
from src.playlist.playlist_view import PlaylistView

# ---------------------------------------------------------------------------
# Tests for PlaylistView delete, rename and edit actions.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression test: renaming a playlist in the tree (double-click/F2) used
# ---------------------------------------------------------------------------


class _FakePlaylist(SimpleNamespace):
    pass


def _playlists():
    return [_FakePlaylist(playlist_id=1, playlist_name="Old Name", parent_id=None, is_smart=False, track_count=0)]


class _FakeControllerRename:
    def __init__(self, playlists):
        self._by_id = {p.playlist_id: p for p in playlists}
        self.updates = []
        self.get = SimpleNamespace(get_all_entities=self._get_all_entities, get_entity_object=lambda entity, playlist_id: self._by_id.get(playlist_id))
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


def _find_item_rename(tree, playlist_id):
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        item = it.value()
        data = item.data(0, Qt.UserRole)
        if data and data[1] == playlist_id:
            return item
        it += 1
    return None


@pytest.fixture
def view_rename(qapp):
    v = PlaylistView(_FakeControllerRename(_playlists()))
    yield v
    v.deleteLater()


def test_renaming_item_persists_to_database(view_rename):
    item = _find_item_rename(view_rename.tree, 1)

    item.setText(0, "New Name")

    assert ("Playlist", 1, {"playlist_name": "New Name"}) in view_rename.controller.updates
    assert view_rename.controller._by_id[1].playlist_name == "New Name"


def test_renaming_to_blank_reverts_and_does_not_save(view_rename):
    item = _find_item_rename(view_rename.tree, 1)

    item.setText(0, "   ")

    assert not any(u[0] == "Playlist" and "playlist_name" in u[2] for u in view_rename.controller.updates)
    assert item.text(0) == "Old Name"


# ---------------------------------------------------------------------------
# Tests for PlaylistView drag-and-drop reparenting.
# ---------------------------------------------------------------------------


class _FakePlaylistDrop(SimpleNamespace):
    pass


def _playlists_drop():
    # root -> child ; and a separate "new parent" at the root
    return [
        _FakePlaylistDrop(playlist_id=1, playlist_name="root", parent_id=None, is_smart=False, track_count=0),
        _FakePlaylistDrop(playlist_id=2, playlist_name="child", parent_id=1, is_smart=False, track_count=3),
        _FakePlaylistDrop(playlist_id=3, playlist_name="new parent", parent_id=None, is_smart=False, track_count=0),
    ]


class _FakeControllerDrop:
    def __init__(self, playlists):
        self._by_id = {p.playlist_id: p for p in playlists}
        self.updates = []

        self.get = SimpleNamespace(get_all_entities=self._get_all_entities, get_entity_object=lambda entity, playlist_id: self._by_id.get(playlist_id))
        self.update = SimpleNamespace(update_entity=self._update_entity)
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()

    def _get_all_entities(self, entity, **kwargs):
        # PlaylistView also queries SmartPlaylist (auto-refresh-on-start
        # check) -- these tests only care about Playlist listing.
        if entity != "Playlist":
            return []
        return list(self._by_id.values())

    def _update_entity(self, entity, entity_id, **fields):
        self.updates.append((entity, entity_id, fields))
        if "parent_id" in fields:
            self._by_id[entity_id].parent_id = fields["parent_id"]


class _FakeDropEvent:
    def __init__(self, pos):
        self._pos = pos
        self.dropAction = Qt.MoveAction
        self.accepted = None

    def pos(self):
        return self._pos

    def setDropAction(self, action):
        self.dropAction = action

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _find_item_drop(tree, playlist_id):
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    it = QTreeWidgetItemIterator(tree)
    while it.value():
        item = it.value()
        data = item.data(0, Qt.UserRole)
        if data and data[1] == playlist_id:
            return item
        it += 1
    return None


@pytest.fixture
def view_drop(qapp):
    v = PlaylistView(_FakeControllerDrop(_playlists_drop()))
    yield v
    v.deleteLater()


def test_drop_moves_item_under_new_parent_in_place(view_drop):
    child = _find_item_drop(view_drop.tree, 2)
    new_parent = _find_item_drop(view_drop.tree, 3)
    assert child.parent() is _find_item_drop(view_drop.tree, 1)

    view_drop.tree.setCurrentItem(child)
    pos = view_drop.tree.visualItemRect(new_parent).center()
    event = _FakeDropEvent(pos)

    view_drop.handle_drop(event)

    # DB was told about the move...
    assert ("Playlist", 2, {"parent_id": 3}) in view_drop.controller.updates
    # ...and the tree item is now under the new parent, without any reload.
    moved = _find_item_drop(view_drop.tree, 2)
    assert moved is child
    assert moved.parent() is new_parent
    assert child not in [_find_item_drop(view_drop.tree, 1).child(i) for i in range(_find_item_drop(view_drop.tree, 1).childCount())]


def test_drop_reports_ignore_action_so_qt_does_not_remove_the_moved_row(view_drop):
    """The InternalMove tree would run clearOrRemove() on a MoveAction drop,
    deleting the row handle_drop just re-parented. handle_drop must downgrade
    the action so Qt's startDrag() leaves the tree untouched."""
    child = _find_item_drop(view_drop.tree, 2)
    new_parent = _find_item_drop(view_drop.tree, 3)

    view_drop.tree.setCurrentItem(child)
    event = _FakeDropEvent(view_drop.tree.visualItemRect(new_parent).center())

    view_drop.handle_drop(event)

    assert event.accepted is True
    assert event.dropAction == Qt.IgnoreAction


def test_drop_on_empty_space_reparents_to_root(view_drop):
    child = _find_item_drop(view_drop.tree, 2)
    view_drop.tree.setCurrentItem(child)
    # A point below every row maps to no item -> drop to top level.
    event = _FakeDropEvent(QPoint(5, view_drop.tree.viewport().height() + 200))

    view_drop.handle_drop(event)

    assert ("Playlist", 2, {"parent_id": None}) in view_drop.controller.updates
    moved = _find_item_drop(view_drop.tree, 2)
    assert moved.parent() is None
    assert event.dropAction == Qt.IgnoreAction


def test_drop_between_rows_makes_a_sibling_not_a_child(view_drop, monkeypatch):
    from PySide6.QtWidgets import QAbstractItemView

    child = _find_item_drop(view_drop.tree, 2)
    new_parent = _find_item_drop(view_drop.tree, 3)
    view_drop.tree.setCurrentItem(child)
    monkeypatch.setattr(view_drop.tree, "dropIndicatorPosition", lambda: QAbstractItemView.BelowItem)

    view_drop.handle_drop(_FakeDropEvent(view_drop.tree.visualItemRect(new_parent).center()))

    # "new parent" is top-level, so a drop below it lands at the top level too.
    assert ("Playlist", 2, {"parent_id": None}) in view_drop.controller.updates
    assert _find_item_drop(view_drop.tree, 2).parent() is None


def test_drop_moves_every_selected_playlist(view_drop):
    root = _find_item_drop(view_drop.tree, 1)
    child = _find_item_drop(view_drop.tree, 2)
    new_parent = _find_item_drop(view_drop.tree, 3)
    view_drop.tree.setCurrentItem(child)
    root.setSelected(True)
    child.setSelected(True)

    view_drop.handle_drop(_FakeDropEvent(view_drop.tree.visualItemRect(new_parent).center()))

    # child is under root, which also moves, so only root itself is reparented.
    assert ("Playlist", 1, {"parent_id": 3}) in view_drop.controller.updates
    assert not any(u[1] == 2 for u in view_drop.controller.updates)
    assert root.parent() is new_parent
    assert child.parent() is root


def test_drop_past_max_depth_is_refused(qapp, monkeypatch):
    monkeypatch.setattr("src.playlist.playlist_tree_dnd.show_status_message", lambda *a: None)
    depth = PlaylistView.MAX_HIERARCHY_DEPTH
    chain = [_FakePlaylistDrop(playlist_id=i, playlist_name=f"level {i}", parent_id=i - 1 or None, is_smart=False, track_count=0) for i in range(1, depth + 2)]
    loose = _FakePlaylistDrop(playlist_id=100, playlist_name="loose", parent_id=None, is_smart=False, track_count=0)
    view_drop = PlaylistView(_FakeControllerDrop([*chain, loose]))
    view_drop.tree.expandAll()
    deepest = _find_item_drop(view_drop.tree, depth + 1)
    view_drop.tree.setCurrentItem(_find_item_drop(view_drop.tree, 100))
    event = _FakeDropEvent(view_drop.tree.visualItemRect(deepest).center())

    view_drop.handle_drop(event)

    assert event.accepted is False
    assert not view_drop.controller.updates
    view_drop.deleteLater()


# ---------------------------------------------------------------------------
# Regression test: playlists nested past MAX_HIERARCHY_DEPTH used to be
# ---------------------------------------------------------------------------


class _FakePlaylistCap(SimpleNamespace):
    pass


def _deep_chain(depth_count):
    """A straight-line chain of `depth_count` playlists, each parented to the last."""
    playlists = []
    parent_id = None
    for i in range(1, depth_count + 1):
        playlists.append(_FakePlaylistCap(playlist_id=i, playlist_name=f"level {i}", parent_id=parent_id, is_smart=False, track_count=0))
        parent_id = i
    return playlists


class _FakeControllerCap:
    def __init__(self, playlists):
        self._playlists = playlists
        self.get = SimpleNamespace(get_all_entities=lambda entity, **kwargs: self._playlists if entity == "Playlist" else [], get_entity_object=lambda entity, **kwargs: None)
        self.update = SimpleNamespace()
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()


def test_playlists_past_max_depth_log_a_warning(qapp, caplog):
    # MAX_HIERARCHY_DEPTH is 8 -- 10 levels guarantees at least one hidden node.
    playlists = _deep_chain(PlaylistView.MAX_HIERARCHY_DEPTH + 2)

    with caplog.at_level(logging.WARNING, logger="musiclib"):
        view = PlaylistView(_FakeControllerCap(playlists))

    assert any("beyond the max hierarchy depth" in r.message for r in caplog.records)
    view.deleteLater()


# ---------------------------------------------------------------------------
# Regression test: an empty playlist library used to render a blank tree
# ---------------------------------------------------------------------------


class _FakeControllerState:
    def __init__(self):
        self.get = SimpleNamespace(get_all_entities=lambda entity, **kwargs: [], get_entity_object=lambda entity, **kwargs: None)
        self.update = SimpleNamespace()
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()


@pytest.fixture
def view_state(qapp):
    v = PlaylistView(_FakeControllerState())
    yield v
    v.deleteLater()


def test_empty_library_shows_placeholder_item(view_state):
    assert view_state.tree.topLevelItemCount() == 1
    item = view_state.tree.topLevelItem(0)
    assert "No playlists yet" in item.text(0)
    assert item.data(0, Qt.UserRole) is None  # not mistakable for a real playlist row


# ---------------------------------------------------------------------------
# Regression test: if creating a smart playlist fails partway through
# ---------------------------------------------------------------------------


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


class _FakeControllerRollback:
    def __init__(self, fail_on_smart_playlist=True):
        self._fail_on_smart_playlist = fail_on_smart_playlist
        self._next_id = 1
        self.created_playlists = {}
        self.deleted = []
        self.get = SimpleNamespace(get_all_entities=lambda entity, **kwargs: [], get_entity_object=lambda entity, **kwargs: None)
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
def view_rollback(qapp, monkeypatch):
    monkeypatch.setattr(playlist_view_module, "SmartPlaylistCreateDialog", _FakeSmartCreateDialog)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    v = PlaylistView(_FakeControllerRollback())
    yield v
    v.deleteLater()


def test_failed_smart_playlist_creation_removes_orphan_playlist(view_rollback):
    view_rollback.create_smart_playlist()

    assert view_rollback.controller.created_playlists == {}
    assert ("Playlist", 1) in view_rollback.controller.deleted


# ---------------------------------------------------------------------------
# Tests for exporting a multi-selection: every playlist, one summary message.
# ---------------------------------------------------------------------------


def test_export_writes_every_selected_playlist_and_shows_one_summary(view, monkeypatch):
    exported_ids = []
    monkeypatch.setattr(playlist_view_module.PlaylistExporter, "export_playlist", lambda self, playlist_id: exported_ids.append(playlist_id) or playlist_id != 2)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[2]))
    view.tree.setCurrentItem(_find_item(view.tree, 1))
    _find_item(view.tree, 2).setSelected(True)
    _find_item(view.tree, 3).setSelected(True)

    view.export_selected_playlist()

    assert sorted(exported_ids) == [1, 2, 3]
    assert len(warnings) == 1
    assert "2 playlists exported, 1 skipped: 'beta'" in warnings[0]


def test_export_of_one_playlist_keeps_the_exporter_messages(view, monkeypatch):
    calls = []
    monkeypatch.setattr(view.exporter, "export_playlist", lambda playlist_id: calls.append(playlist_id) or True)
    view.tree.setCurrentItem(_find_item(view.tree, 3))

    view.export_selected_playlist()

    assert calls == [3]
