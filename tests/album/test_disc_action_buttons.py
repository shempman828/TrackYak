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

from src.album.disc import disc_view
from src.album.disc.disc_view import DiscTabView


class _StubAlbum:
    def __init__(self, album_id=1, album_name="Test Album", release_type=None):
        self.album_id = album_id
        self.album_name = album_name
        self.release_type = release_type


class _StubDisc:
    def __init__(self, disc_id=10, disc_number=1, disc_title=None):
        self.disc_id = disc_id
        self.disc_number = disc_number
        self.disc_title = disc_title


class _StubSession:
    def __init__(self):
        self.expire_all_calls = 0

    def expire_all(self):
        self.expire_all_calls += 1


class _StubGet:
    def __init__(self, discs):
        self._discs = discs
        self.session = _StubSession()

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "Disc":
            return list(self._discs)
        return []


class _RecordingUpdate:
    def __init__(self):
        self.calls = []
        self.bulk_calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return True

    def update_entities_bulk_with_fallback(self, model_name, rows):
        self.bulk_calls.append((model_name, rows))
        return len(rows), []


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

    def exec(self):
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
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2), _StubDisc(disc_id=3, disc_number=3)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)

    _select_disc_row(view, disc_id=2)
    view.edit_disc()

    assert controller.update.calls == [("Disc", 2, {"disc_title": "New Title"})]


def test_remove_disc_targets_disc_selected_in_track_list(qapp, monkeypatch):
    _confirm_yes(monkeypatch)
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


def test_remove_disc_expires_session_so_stale_track_disc_ids_refresh(qapp, monkeypatch):
    """The DB's FK cascade (ON DELETE SET NULL) happens outside SQLAlchemy's
    unit of work, so already-loaded Track objects must be expired or the
    Tracks tab keeps showing them under the deleted disc."""
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    _confirm_yes(monkeypatch)

    _select_disc_row(view, disc_id=2)
    view.remove_disc()

    assert controller.get.session.expire_all_calls == 1


def test_create_track_display_does_not_stack_empty_placeholder(qapp):
    """Calling create_track_display() twice on an empty album must not leave
    two "No tracks found" labels in the layout -- just the track display
    widget plus a single placeholder."""
    view = DiscTabView(_StubAlbum(), _StubController(discs=[]))

    view.create_track_display()
    view.create_track_display()

    assert view.track_layout.count() == 2


# ---------------------------------------------------------------------------
# Finalize pass: refresh, status, errors, disc numbers, confirmation
# ---------------------------------------------------------------------------


class _RecordingAdd:
    def __init__(self):
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return True


def _confirm_yes(monkeypatch):
    monkeypatch.setattr(disc_view.QMessageBox, "question", staticmethod(lambda *a, **k: disc_view.QMessageBox.Yes))


def test_refresh_keeps_the_same_track_tree(qapp):
    """An open track edit dialog is a child of the tree, so a refresh must not replace the tree."""
    view = DiscTabView(_StubAlbum(), _StubController(discs=[_StubDisc()]))
    tree = view.track_display

    view.refresh_view()

    assert view.track_display is tree


def test_add_disc_shows_result_and_does_not_emit_tracks_changed(qapp, monkeypatch):
    controller = _StubController(discs=[])
    controller.add = _RecordingAdd()
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)
    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.add_disc()

    assert controller.add.calls == [("Disc", {"album_id": 1, "disc_number": 1, "disc_title": "New Title"})]
    assert view.status_label.text() == "Added disc 1"
    assert emitted == []


def test_load_data_db_error_shows_status_and_does_not_emit(qapp):
    from sqlalchemy.exc import SQLAlchemyError

    controller = _StubController(discs=[])
    view = DiscTabView(_StubAlbum(), controller)
    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    def _fail(*_a, **_k):
        raise SQLAlchemyError("boom")

    controller.get.get_all_entities = _fail
    view.refresh_view()

    assert "Could not load" in view.status_label.text()
    assert emitted == []
    view.update_stats()  # lists stay defined after a failed load


def test_next_disc_number_ignores_missing_numbers(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[_StubDisc(disc_id=1, disc_number=None), _StubDisc(disc_id=2, disc_number=2)]))

    assert view._next_disc_number() == 3


def test_remove_empty_disc_asks_and_cancel_keeps_it(qapp, monkeypatch):
    controller = _StubController(discs=[_StubDisc()])
    view = DiscTabView(_StubAlbum(), controller)
    asked = []

    def _cancel(*a, **k):
        asked.append(a[2])
        return disc_view.QMessageBox.Cancel

    monkeypatch.setattr(disc_view.QMessageBox, "question", staticmethod(_cancel))

    view.remove_disc()

    assert asked == ["Remove Disc 1?"]
    assert controller.delete.calls == []


def test_remove_disc_moves_later_discs_down_and_emits(qapp, monkeypatch):
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2), _StubDisc(disc_id=3, disc_number=3)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    _confirm_yes(monkeypatch)
    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.remove_disc(discs[0])

    assert controller.delete.calls == [("Disc", 1, {})]
    assert controller.update.bulk_calls == [("Disc", [{"disc_id": 2, "disc_number": 1}, {"disc_id": 3, "disc_number": 2}])]
    assert view.status_label.text() == "Removed Disc 1"
    assert emitted == [True]


def test_remove_disc_does_not_shift_when_duplicate_number_remains(qapp, monkeypatch):
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=1), _StubDisc(disc_id=3, disc_number=2)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    _confirm_yes(monkeypatch)

    view.remove_disc(discs[0])

    assert controller.update.bulk_calls == []


def test_multi_disc_buttons_follow_selection(qapp):
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2)]
    view = DiscTabView(_StubAlbum(), _StubController(discs=discs))

    assert not view.edit_disc_btn.isEnabled()

    _select_disc_row(view, disc_id=2)

    assert view.edit_disc_btn.isEnabled()
    assert view.remove_disc_btn.isEnabled()


def test_edit_disc_number_change_is_written_and_emits(qapp, monkeypatch):
    class _RenumberDialog(_FakeDiscDialog):
        def get_disc_data(self):
            return {"disc_number": 5, "disc_title": None}

    controller = _StubController(discs=[_StubDisc(disc_id=7, disc_number=1)])
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _RenumberDialog)
    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.edit_disc()

    assert controller.update.calls == [("Disc", 7, {"disc_title": None, "disc_number": 5})]
    assert emitted == [True]


def test_tree_disc_request_edits_that_disc(qapp, monkeypatch):
    discs = [_StubDisc(disc_id=1, disc_number=1), _StubDisc(disc_id=2, disc_number=2)]
    controller = _StubController(discs=discs)
    view = DiscTabView(_StubAlbum(), controller)
    monkeypatch.setattr(disc_view, "DiscEditDialog", _FakeDiscDialog)

    view.track_display.edit_disc_requested.emit(discs[1])

    assert controller.update.calls == [("Disc", 2, {"disc_title": "New Title"})]


def test_layout_change_updates_stats_and_emits(qapp):
    view = DiscTabView(_StubAlbum(), _StubController(discs=[]))
    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.track_display.layout_changed.emit()

    assert emitted == [True]
    assert view.status_label.text() == "Saved the new track order"


# ---------------------------------------------------------------------------
# DiscEditDialog
# ---------------------------------------------------------------------------


def test_disc_dialog_refuses_a_taken_number(qapp):
    from src.album.disc.disc_edit import DiscEditDialog

    dialog = DiscEditDialog(_StubAlbum(), next_number=3, taken_numbers=[1, 2])
    dialog.number_input.setValue(2)

    dialog.accept()

    assert dialog.result() != QDialog.Accepted
    assert not dialog.error_label.isHidden()


def test_disc_dialog_allows_the_edited_discs_own_number(qapp):
    from src.album.disc.disc_edit import DiscEditDialog

    disc = _StubDisc(disc_id=1, disc_number=1, disc_title="  Side  ")
    dialog = DiscEditDialog(_StubAlbum(), disc=disc, taken_numbers=[1, 2])
    dialog.title_input.setText("  Live  ")

    dialog.accept()

    assert dialog.result() == QDialog.Accepted
    assert dialog.get_disc_data() == {"disc_number": 1, "disc_title": "Live"}
