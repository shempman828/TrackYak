"""
Regression tests for TrackSortingDisplay (disc_sorting.py).

Motivating bugs:
- _delete_tracks logged but did not act on a failed batch delete: file
  deletion and track_deleted still proceeded even when the DB rows were
  never removed.
- show_context_menu had no branch for a right-clicked virtual row that
  wasn't already selected, so the menu reflected a stale prior selection.
- Side labels sorted as plain strings ("1", "10", "2" instead of numeric
  order).
- Disc view nested tracks under a separate "Side A"/"Side B" header row,
  which read as needless depth for the common single (Disc 1 / Side A /
  Side B, one track each). Side is now folded into the number column
  ("A1", "B1", ...) with tracks listed directly under the disc.
- Auto-Number Tracks didn't know about singles: a detected single (by
  release_type or the common one-disc/two-track shape) with no side set
  yet is now auto-split into an A/B pair instead of numbered 1/2.
"""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from src.album.disc.disc_sorting import TrackSortingDisplay, _side_sort_key


def _track(track_id, name="Track", disc_id=None, track_number=1, side=None, duration="3:00"):
    return SimpleNamespace(track_id=track_id, track_name=name, disc_id=disc_id, track_number=track_number, side=side, duration_formatted=duration, track_file_path=None)


class _FakeDelete:
    def __init__(self, delete_ok=True):
        self.delete_ok = delete_ok
        self.calls = []

    def delete_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.delete_ok

    def delete_file(self, file_path):
        return True


class _FakeGet:
    def __init__(self, tracks):
        self._tracks = {t.track_id: t for t in tracks}

    def get_entity_object(self, model_name, track_id=None, **kwargs):
        return self._tracks.get(track_id)


class _FakeUpdate:
    def __init__(self):
        self.rows = None

    def update_entities_bulk_with_fallback(self, model_name, rows):
        self.rows = rows
        return len(rows), []


class _FakeController:
    def __init__(self, tracks, delete_ok=True):
        self.get = _FakeGet(tracks)
        self.delete = _FakeDelete(delete_ok=delete_ok)
        self.update = _FakeUpdate()


def test_side_sort_key_orders_numeric_labels_naturally():
    labels = ["10", "2", "1"]
    assert sorted(labels, key=_side_sort_key) == ["1", "2", "10"]


def test_side_sort_key_falls_back_to_string_order_for_letters():
    labels = ["B", "A"]
    assert sorted(labels, key=_side_sort_key) == ["A", "B"]


def test_delete_tracks_does_not_emit_or_delete_files_when_batch_delete_fails(qapp, monkeypatch):
    track = _track(1)
    controller = _FakeController([track], delete_ok=False)
    view = TrackSortingDisplay([track], controller=controller)

    monkeypatch.setattr("src.album.disc.disc_sorting.confirm_delete_with_file_option", lambda *a, **k: "db_only")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    emitted = []
    view.track_deleted.connect(lambda: emitted.append(True))

    view._delete_tracks([1])

    assert emitted == []


def test_delete_tracks_emits_when_batch_delete_succeeds(qapp, monkeypatch):
    track = _track(1)
    controller = _FakeController([track], delete_ok=True)
    view = TrackSortingDisplay([track], controller=controller)

    monkeypatch.setattr("src.album.disc.disc_sorting.confirm_delete_with_file_option", lambda *a, **k: "db_only")

    emitted = []
    view.track_deleted.connect(lambda: emitted.append(True))

    view._delete_tracks([1])

    assert emitted == [True]


def test_show_context_menu_right_click_on_unselected_virtual_row_shows_virtual_note(qapp, monkeypatch):
    """Before the fix, right-clicking an unselected virtual row hit neither
    branch: physical_ids stayed empty and has_virtual_selected stayed False,
    so the menu showed no "Edit Track" entry at all -- not even the
    disabled virtual-track note."""
    physical = _track(1, disc_id=None)
    controller = _FakeController([physical])
    view = TrackSortingDisplay([physical], controller=controller)

    virtual_track = _track(2, name="Virtual")
    disc_item = view.topLevelItem(0)
    view._create_track_node(disc_item, {"track": virtual_track, "is_virtual": True, "track_number": 1})
    virtual_item = disc_item.child(disc_item.childCount() - 1)

    view.clearSelection()
    view.itemAt = lambda _pos: virtual_item

    built_menus = []
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    class _RecordingMenu(QMenu):
        def exec(self, *a, **k):
            built_menus.append(self)

    monkeypatch.setattr("src.album.disc.disc_sorting.QMenu", _RecordingMenu)

    view.show_context_menu(QPoint(0, 0))

    assert built_menus, "show_context_menu should have built a QMenu"
    menu_texts = [a.text() for a in built_menus[0].actions()]
    assert any("virtual" in t.lower() for t in menu_texts)


def test_populate_tree_folds_side_into_track_number_with_no_side_header(qapp):
    """Side-grouped tracks must sit directly under the disc row, labeled
    "A1"/"B1", instead of nested one level deeper under a "Side A" header."""
    track_a = _track(1, name="A-side", side="A", track_number=1)
    track_b = _track(2, name="B-side", side="B", track_number=1)
    controller = _FakeController([track_a, track_b])
    view = TrackSortingDisplay([track_a, track_b], controller=controller)

    assert view.topLevelItemCount() == 1
    disc_item = view.topLevelItem(0)
    assert disc_item.text(0) == "Unassigned Tracks"
    assert disc_item.childCount() == 2

    labels = {disc_item.child(i).text(0) for i in range(disc_item.childCount())}
    assert labels == {"A1", "B1"}
    # No intermediate "Side A"/"Side B" header row.
    for i in range(disc_item.childCount()):
        assert isinstance(disc_item.child(i).data(0, Qt.UserRole), int)


def test_assign_absolute_track_numbers_auto_splits_detected_single(qapp, monkeypatch):
    """A detected single with two bare (no-side) tracks gets auto-split into
    an A/B pair on Auto-Number Tracks, instead of plain 1/2 with no side."""
    track_1 = _track(1, name="Track One", side=None, track_number=1)
    track_2 = _track(2, name="Track Two", side=None, track_number=2)
    controller = _FakeController([track_1, track_2])
    view = TrackSortingDisplay([track_1, track_2], controller=controller, release_type="Single")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    view.assign_absolute_track_numbers()

    rows_by_id = {row["track_id"]: row for row in controller.update.rows}
    assert rows_by_id[1]["side"] == "A"
    assert rows_by_id[1]["track_number"] == 1
    assert rows_by_id[1]["absolute_track_number"] == 1
    assert rows_by_id[2]["side"] == "B"
    assert rows_by_id[2]["track_number"] == 1
    assert rows_by_id[2]["absolute_track_number"] == 2


def test_assign_absolute_track_numbers_does_not_auto_split_non_single(qapp, monkeypatch):
    """Two bare tracks on a non-single album keep plain sequential numbering
    with no side written."""
    track_1 = _track(1, name="Track One", side=None, track_number=1)
    track_2 = _track(2, name="Track Two", side=None, track_number=2)
    controller = _FakeController([track_1, track_2])
    view = TrackSortingDisplay([track_1, track_2], controller=controller, release_type="Album")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    view.assign_absolute_track_numbers()

    rows_by_id = {row["track_id"]: row for row in controller.update.rows}
    assert "side" not in rows_by_id[1]
    assert rows_by_id[1]["track_number"] == 1
    assert "side" not in rows_by_id[2]
    assert rows_by_id[2]["track_number"] == 2


# ---------------------------------------------------------------------------
# Finalize pass: drop targets, grouping, write only changes, menus, keys
# ---------------------------------------------------------------------------


def _disc(disc_id, disc_number, disc_title=None):
    return SimpleNamespace(disc_id=disc_id, disc_number=disc_number, disc_title=disc_title)


def _virtual_link(track, disc_number):
    return SimpleNamespace(track=track, virtual_disc_number=disc_number, virtual_track_number=1, virtual_side=None)


def _group(view, title):
    for i in range(view.topLevelItemCount()):
        if view.topLevelItem(i).text(0) == title:
            return view.topLevelItem(i)
    raise AssertionError(f"No group {title!r}")


def test_track_rows_and_root_do_not_accept_drops(qapp):
    """A drop onto a track row nested it; a drop between discs made it a top-level row."""
    track = _track(1, disc_id=10)
    view = TrackSortingDisplay([track], discs=[_disc(10, 1)], controller=_FakeController([track]))

    assert not view.invisibleRootItem().flags() & Qt.ItemIsDropEnabled
    row = _group(view, "Disc 1").child(0)
    assert not row.flags() & Qt.ItemIsDropEnabled


def test_virtual_only_group_refuses_drops_and_unassigned_accepts(qapp):
    physical = _track(1, disc_id=10)
    virtual = _track(2, name="Borrowed")
    view = TrackSortingDisplay([physical], discs=[_disc(10, 1)], virtual_links=[_virtual_link(virtual, 2)], controller=_FakeController([physical]))

    assert _group(view, "Disc 1").flags() & Qt.ItemIsDropEnabled
    assert _group(view, "Unassigned Tracks").flags() & Qt.ItemIsDropEnabled
    assert not _group(view, "Disc 2").flags() & Qt.ItemIsDropEnabled


def test_unassigned_group_shown_when_album_has_discs(qapp):
    track = _track(1, disc_id=10)
    view = TrackSortingDisplay([track], discs=[_disc(10, 1)], controller=_FakeController([track]))

    assert _group(view, "Unassigned Tracks").childCount() == 0


def test_duplicate_disc_numbers_keep_each_tracks_disc_on_drop(qapp):
    track_a = _track(1, disc_id=10, track_number=1)
    track_b = _track(2, disc_id=11, track_number=1)
    controller = _FakeController([track_a, track_b])
    view = TrackSortingDisplay([track_a, track_b], discs=[_disc(10, 1), _disc(11, 1)], controller=controller)

    groups = [view.topLevelItem(i) for i in range(view.topLevelItemCount()) if view.topLevelItem(i).text(0) == "Disc 1"]
    assert len(groups) == 2

    view._persist_drop()

    rows_by_id = {row["track_id"]: row for row in controller.update.rows}
    assert rows_by_id[1]["disc_id"] == 10
    assert rows_by_id[2]["disc_id"] == 11


def test_persist_drop_writes_only_changed_rows_and_emits(qapp):
    unchanged = SimpleNamespace(**vars(_track(1, disc_id=10, track_number=1)), absolute_track_number=1)
    moved = SimpleNamespace(**vars(_track(2, disc_id=None, track_number=1)), absolute_track_number=2)
    controller = _FakeController([unchanged, moved])
    view = TrackSortingDisplay([unchanged, moved], discs=[_disc(10, 1)], controller=controller)
    emitted = []
    view.layout_changed.connect(lambda: emitted.append(True))

    # Move the unassigned track under Disc 1.
    unassigned = _group(view, "Unassigned Tracks")
    _group(view, "Disc 1").addChild(unassigned.takeChild(0))
    view._persist_drop()

    assert [row["track_id"] for row in controller.update.rows] == [2]
    assert controller.update.rows[0]["disc_id"] == 10
    assert emitted == [True]


def test_persist_drop_with_no_change_does_not_write(qapp):
    track = SimpleNamespace(**vars(_track(1, disc_id=10, track_number=1)), absolute_track_number=1)
    controller = _FakeController([track])
    view = TrackSortingDisplay([track], discs=[_disc(10, 1)], controller=controller)

    view._persist_drop()

    assert controller.update.rows is None


def test_renumber_warns_when_rows_fail(qapp, monkeypatch):
    track = _track(1, track_number=5)
    controller = _FakeController([track])
    controller.update.update_entities_bulk_with_fallback = lambda model, rows: (0, rows)
    view = TrackSortingDisplay([track], controller=controller)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[1])))

    view.assign_absolute_track_numbers()

    assert warnings == ["Some Tracks Not Saved"]


def test_delete_uses_loaded_tracks_without_query(qapp, monkeypatch):
    track = _track(1)
    controller = _FakeController([track])
    controller.get.get_entity_object = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected query"))
    view = TrackSortingDisplay([track], controller=controller)
    monkeypatch.setattr("src.album.disc.disc_sorting.confirm_delete_with_file_option", lambda *a, **k: "db_only")

    view._delete_tracks([1])

    assert controller.delete.calls == [("Track", {"entity_ids": [1]})]


def test_set_data_keeps_selected_tracks(qapp):
    track = _track(1, disc_id=10)
    view = TrackSortingDisplay([track], discs=[_disc(10, 1)], controller=_FakeController([track]))
    _group(view, "Disc 1").child(0).setSelected(True)

    view.set_data([track], discs=[_disc(10, 1)])

    assert _group(view, "Disc 1").child(0).isSelected()


def test_disc_group_context_menu_requests_disc_actions(qapp, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    disc = _disc(10, 1)
    view = TrackSortingDisplay([], discs=[disc], controller=_FakeController([]))
    group = _group(view, "Disc 1")
    view.itemAt = lambda _pos: group
    menus = []

    class _RecordingMenu(QMenu):
        def exec(self, *a, **k):
            menus.append(self)

    monkeypatch.setattr("src.album.disc.disc_sorting.QMenu", _RecordingMenu)
    requested = []
    view.edit_disc_requested.connect(lambda d: requested.append(("edit", d)))
    view.remove_disc_requested.connect(lambda d: requested.append(("remove", d)))

    view.show_context_menu(QPoint(0, 0))
    for action in menus[0].actions():
        action.trigger()

    assert requested == [("edit", disc), ("remove", disc)]


def test_double_click_disc_group_requests_edit(qapp):
    disc = _disc(10, 1)
    view = TrackSortingDisplay([], discs=[disc], controller=_FakeController([]))
    requested = []
    view.edit_disc_requested.connect(requested.append)

    view.itemDoubleClicked.emit(_group(view, "Disc 1"), 0)

    assert requested == [disc]


def test_delete_key_deletes_selected_tracks(qapp, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    track = _track(1)
    view = TrackSortingDisplay([track], controller=_FakeController([track]))
    _group(view, "Unassigned Tracks").child(0).setSelected(True)
    deleted = []
    monkeypatch.setattr(view, "_delete_tracks", deleted.append)

    view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))

    assert deleted == [[1]]
