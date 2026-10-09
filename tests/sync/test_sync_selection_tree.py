"""
SyncSelectionTree: two-column checklist with a display-only "something inside
is selected" hint, per-section "N of M selected" counts, and a name filter.
"""

from PySide6.QtCore import Qt
import pytest

from src.sync.sync_selection_tree import COUNT_COLUMN, SyncSelectionTree

pytestmark = pytest.mark.usefixtures("qapp")


def _tree():
    tree = SyncSelectionTree()
    section = tree.add_section("PLAYLISTS")
    parent = tree.make_item(section, {"kind": "playlist", "playlist_id": 1, "name": "Work", "track_count": 1200})
    child = tree.make_item(parent, {"kind": "playlist", "playlist_id": 2, "name": "Focus", "track_count": 4})
    other = tree.make_item(section, {"kind": "playlist", "playlist_id": 3, "name": "Road trip", "track_count": 0, "is_smart": True})
    tree.expandAll()
    return tree, section, parent, child, other


def test_count_column_formats_tracks_and_smart_playlists():
    _, _section, parent, _child, other = _tree()
    assert parent.text(COUNT_COLUMN) == "1,200"
    assert other.text(COUNT_COLUMN) == "smart"


def test_partial_hint_marks_parent_without_selecting_it():
    tree, _section, parent, child, _other = _tree()
    child.setCheckState(0, Qt.Checked)

    tree.refresh_partial_states()
    assert parent.checkState(0) == Qt.PartiallyChecked

    child.setCheckState(0, Qt.Unchecked)
    tree.refresh_partial_states()
    assert parent.checkState(0) == Qt.Unchecked


def test_ticked_parent_keeps_its_own_state():
    tree, _section, parent, child, _other = _tree()
    parent.setCheckState(0, Qt.Checked)
    tree.refresh_partial_states()
    assert parent.checkState(0) == Qt.Checked
    assert child.checkState(0) == Qt.Unchecked  # selection never cascades


def test_section_counts():
    tree, section, _parent, child, _other = _tree()
    child.setCheckState(0, Qt.Checked)
    tree.update_section_counts()
    assert section.text(COUNT_COLUMN) == "1 of 3 selected"


def test_filter_keeps_ancestors_of_matches_and_limits_visible_rows():
    tree, _section, parent, child, other = _tree()
    tree.set_filter_text("foc")

    assert not child.isHidden() and not parent.isHidden()
    assert other.isHidden()
    assert list(tree.visible_checkable_items()) == [parent, child]

    tree.set_filter_text("zzz")
    assert not tree.has_visible_rows()

    tree.set_filter_text("")
    assert not other.isHidden()


def test_context_branch_change_sets_descendants_and_signals():
    tree, _section, parent, child, _other = _tree()
    fired = []
    tree.bulkCheckChanged.connect(lambda: fired.append(True))
    tree._set_branch(parent, Qt.Checked)
    assert parent.checkState(0) == Qt.Checked and child.checkState(0) == Qt.Checked
    assert fired == [True]
