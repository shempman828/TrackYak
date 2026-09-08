"""Tests for PlaylistView drag-and-drop reparenting.

Regression: dropping a playlist onto a new parent removed it from its old
location but it never appeared under the new one until the whole tree was
reloaded. handle_drop re-parents the item itself, but the tree runs in
InternalMove mode, so QAbstractItemView.startDrag() would then run its own
clearOrRemove() on the still-selected row and delete the item we had just
moved. The fix reports Qt.IgnoreAction from the drop so Qt leaves the tree
alone.
"""

from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt
import pytest

from src.playlist.playlist_view import PlaylistView


class _FakePlaylist(SimpleNamespace):
    pass


def _playlists():
    # root -> child ; and a separate "new parent" at the root
    return [
        _FakePlaylist(
            playlist_id=1, playlist_name="root", parent_id=None, is_smart=False, track_count=0
        ),
        _FakePlaylist(
            playlist_id=2, playlist_name="child", parent_id=1, is_smart=False, track_count=3
        ),
        _FakePlaylist(
            playlist_id=3, playlist_name="new parent", parent_id=None, is_smart=False, track_count=0
        ),
    ]


class _FakeController:
    def __init__(self, playlists):
        self._by_id = {p.playlist_id: p for p in playlists}
        self.updates = []

        self.get = SimpleNamespace(
            get_all_entities=lambda entity: list(self._by_id.values()),
            get_entity_object=lambda entity, playlist_id: self._by_id.get(playlist_id),
        )
        self.update = SimpleNamespace(update_entity=self._update_entity)
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()

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


def _find_item(tree, playlist_id):
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
def view(qapp):
    v = PlaylistView(_FakeController(_playlists()))
    yield v
    v.deleteLater()


def test_drop_moves_item_under_new_parent_in_place(view):
    child = _find_item(view.tree, 2)
    new_parent = _find_item(view.tree, 3)
    assert child.parent() is _find_item(view.tree, 1)

    view.tree.setCurrentItem(child)
    pos = view.tree.visualItemRect(new_parent).center()
    event = _FakeDropEvent(pos)

    view.handle_drop(event)

    # DB was told about the move...
    assert ("Playlist", 2, {"parent_id": 3}) in view.controller.updates
    # ...and the tree item is now under the new parent, without any reload.
    moved = _find_item(view.tree, 2)
    assert moved is child
    assert moved.parent() is new_parent
    assert child not in [
        _find_item(view.tree, 1).child(i) for i in range(_find_item(view.tree, 1).childCount())
    ]


def test_drop_reports_ignore_action_so_qt_does_not_remove_the_moved_row(view):
    """The InternalMove tree would run clearOrRemove() on a MoveAction drop,
    deleting the row handle_drop just re-parented. handle_drop must downgrade
    the action so Qt's startDrag() leaves the tree untouched."""
    child = _find_item(view.tree, 2)
    new_parent = _find_item(view.tree, 3)

    view.tree.setCurrentItem(child)
    event = _FakeDropEvent(view.tree.visualItemRect(new_parent).center())

    view.handle_drop(event)

    assert event.accepted is True
    assert event.dropAction == Qt.IgnoreAction


def test_drop_on_empty_space_reparents_to_root(view):
    child = _find_item(view.tree, 2)
    view.tree.setCurrentItem(child)
    # A point below every row maps to no item -> drop to top level.
    event = _FakeDropEvent(QPoint(5, view.tree.viewport().height() + 200))

    view.handle_drop(event)

    assert ("Playlist", 2, {"parent_id": None}) in view.controller.updates
    moved = _find_item(view.tree, 2)
    assert moved.parent() is None
    assert event.dropAction == Qt.IgnoreAction
