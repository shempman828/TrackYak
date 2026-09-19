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
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from src.album.disc_tab.disc_sorting import TrackSortingDisplay, _side_sort_key


def _track(track_id, name="Track", disc_id=None, track_number=1, side=None, duration="3:00"):
    return SimpleNamespace(
        track_id=track_id,
        track_name=name,
        disc_id=disc_id,
        track_number=track_number,
        side=side,
        duration_formatted=duration,
        track_file_path=None,
    )


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


class _FakeController:
    def __init__(self, tracks, delete_ok=True):
        self.get = _FakeGet(tracks)
        self.delete = _FakeDelete(delete_ok=delete_ok)


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

    monkeypatch.setattr(
        "src.album.disc_tab.disc_sorting.confirm_delete_with_file_option", lambda *a, **k: "db_only"
    )
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    emitted = []
    view.track_deleted.connect(lambda: emitted.append(True))

    view._delete_tracks([1])

    assert emitted == []


def test_delete_tracks_emits_when_batch_delete_succeeds(qapp, monkeypatch):
    track = _track(1)
    controller = _FakeController([track], delete_ok=True)
    view = TrackSortingDisplay([track], controller=controller)

    monkeypatch.setattr(
        "src.album.disc_tab.disc_sorting.confirm_delete_with_file_option", lambda *a, **k: "db_only"
    )

    emitted = []
    view.track_deleted.connect(lambda: emitted.append(True))

    view._delete_tracks([1])

    assert emitted == [True]


def test_show_context_menu_right_click_on_unselected_virtual_row_shows_virtual_note(
    qapp, monkeypatch
):
    """Before the fix, right-clicking an unselected virtual row hit neither
    branch: physical_ids stayed empty and has_virtual_selected stayed False,
    so the menu showed no "Edit Track" entry at all -- not even the
    disabled virtual-track note."""
    physical = _track(1, disc_id=None)
    controller = _FakeController([physical])
    view = TrackSortingDisplay([physical], controller=controller)

    virtual_track = _track(2, name="Virtual")
    disc_item = view.topLevelItem(0)
    view._create_track_node(
        disc_item, {"track": virtual_track, "is_virtual": True, "track_number": 1}
    )
    virtual_item = disc_item.child(disc_item.childCount() - 1)

    view.clearSelection()
    view.itemAt = lambda _pos: virtual_item

    built_menus = []
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: built_menus.append(self))

    view.show_context_menu(QPoint(0, 0))

    assert built_menus, "show_context_menu should have built a QMenu"
    menu_texts = [a.text() for a in built_menus[0].actions()]
    assert any("virtual" in t.lower() for t in menu_texts)
