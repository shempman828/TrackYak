"""Regression tests for the album Disc tab's Edit/Remove disc buttons.

Bug: "Edit disc" and "Remove disc" never became active. The buttons start
disabled and were only re-enabled inside DiscTabView.refresh_view(),
which __init__ never calls -- it calls load_data() directly. So opening an
album that already had discs left both buttons greyed out until the user
manually hit Refresh or added a disc.

Also covers DiscTabView.edit_disc()/remove_disc() acting on the disc
selected in the track list rather than popping a QInputDialog picker.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from src.album.disc_tab import view as disc_view
from src.album.disc_tab.view import DiscTabView


class _StubAlbum:
    def __init__(self, album_id=1, album_name="Test Album"):
        self.album_id = album_id
        self.album_name = album_name


class _StubDisc:
    def __init__(self, disc_id=10, disc_number=1, disc_title=None):
        self.disc_id = disc_id
        self.disc_number = disc_number
        self.disc_title = disc_title


class _StubGet:
    def __init__(self, discs):
        self._discs = discs

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "Disc":
            return list(self._discs)
        return []


class _RecordingUpdate:
    def __init__(self):
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return True


class _RecordingDelete:
    def __init__(self):
        self.calls = []

    def delete_entity(self, model_name, entity_id=None, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return True


class _StubController:
    def __init__(self, discs=()):
        self.get = _StubGet(discs)
        self.update = _RecordingUpdate()
        self.delete = _RecordingDelete()


class _FakeDiscDialog:
    def __init__(self, *args, **kwargs):
        pass

    def exec_(self):
        return QDialog.Accepted

    def get_disc_data(self):
        return {"disc_title": "New Title"}


def _select_disc_row(view, disc_id):
    """Select the top-level disc-header row for disc_id in the track tree."""
    tree = view.track_display
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        disc = item.data(0, Qt.UserRole)
        if disc is not None and disc.disc_id == disc_id:
            item.setSelected(True)
            return
    raise AssertionError(f"No disc row for disc_id={disc_id}")


def test_buttons_enabled_on_open_when_album_has_discs(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[_StubDisc()]))

    assert view.edit_disc_btn.isEnabled()
    assert view.remove_disc_btn.isEnabled()


def test_buttons_disabled_on_open_when_album_has_no_discs(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[]))

    assert not view.edit_disc_btn.isEnabled()
    assert not view.remove_disc_btn.isEnabled()


def test_edit_disc_writes_title_via_controller(qapp, monkeypatch):
    """Single-disc album: no selection needed, the sole disc is the target."""
    disc = _StubDisc(disc_id=42, disc_number=1, disc_title="Old")
    controller = _StubController(discs=[disc])
    view = DiscTabView(_StubAlbum(), controller)

    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)

    view.edit_disc()

    assert controller.update.calls == [("Disc", 42, {"disc_title": "New Title"})]


def test_edit_disc_targets_disc_selected_in_track_list(qapp, monkeypatch):
    """Multi-disc album: Edit Disc acts on the disc highlighted in the tree,
    not via a picker dialog."""
    discs = [
        _StubDisc(disc_id=1, disc_number=1),
        _StubDisc(disc_id=2, disc_number=2),
        _StubDisc(disc_id=3, disc_number=3),
    ]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)

    _select_disc_row(view, disc_id=2)
    view.edit_disc()

    assert controller.update.calls == [("Disc", 2, {"disc_title": "New Title"})]


def test_remove_disc_targets_disc_selected_in_track_list(qapp):
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)

    _select_disc_row(view, disc_id=2)
    view.remove_disc()

    assert controller.delete.calls == [("Disc", 2, {})]


def test_edit_disc_multi_disc_without_selection_is_a_no_op(qapp, monkeypatch):
    """No disc selected and several to choose from: nudge, don't write."""
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)

    view.edit_disc()

    assert controller.update.calls == []
