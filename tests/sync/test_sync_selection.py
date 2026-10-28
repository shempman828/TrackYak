"""Sync selection: the selection mixin, the selection tree, and the items
loader.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QTreeWidgetItem
import pytest

from src.common.cancellable_worker import CancellableWorker
from src.sync import sync_selection_mixin
from src.sync.device_card import format_file_size
from src.sync.sync_items_loader import SyncItemsLoader
from src.sync.sync_selection_mixin import SyncSelectionMixin
from src.sync.sync_selection_tree import COUNT_COLUMN, SyncSelectionTree

# ---------------------------------------------------------------------------
# The Sync view's selection tree/summary mixin.
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.usefixtures("qapp")


class _SummingManager:
    """
    Stand-in for `SyncManager.selection_totals`: plain per-item sum off the
    tree, no dedup -- these hosts never put a track in two playlists, so the
    dedup itself is covered in test_sync_manager.py against a real DB.
    """

    def __init__(self, host):
        self._host = host

    def selection_totals(self, playlist_ids, mood_ids):
        tracks = size = lossless = 0
        duration = 0.0
        for it in self._host._iter_sync_items():
            if it.checkState(0) == Qt.Checked:
                d = it.data(0, Qt.UserRole)
                tracks += d.get("track_count", 0)
                size += d.get("size", 0) or 0
                lossless += d.get("lossless_size", 0) or 0
                duration += d.get("lossless_duration", 0) or 0
        return (tracks, size, lossless, duration)


class _Host(SyncSelectionMixin):
    """Minimal carrier for the mixin: real widgets, no full SyncView."""

    def __init__(self, *, checked=False, enabled=True, bitrate="320"):
        self.sync_tree = SyncSelectionTree()
        self.sync_manager = _SummingManager(self)
        self.track_count_label = QLabel()
        self.transcode_mp3_check = QCheckBox()
        self.transcode_mp3_check.setEnabled(enabled)
        self.transcode_mp3_check.setChecked(checked)
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["320", "256", "192", "128"])
        self.bitrate_combo.setCurrentText(bitrate)

    def _update_sync_button_state(self):  # stubbed dependency
        pass

    def add_item(self, **data):
        data.setdefault("kind", "playlist")
        data.setdefault("track_count", 1)
        seq = self.sync_tree.topLevelItemCount() + 1
        data.setdefault("mood_id" if data["kind"] == "mood" else "playlist_id", seq)
        item = QTreeWidgetItem(self.sync_tree, ["x"])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        item.setData(0, Qt.UserRole, data)
        return item


def test_plain_size_when_transcode_off():  # AC4
    host = _Host(checked=False)
    assert host._selection_size_text(47_000_000, 40_000_000, 180.0) == format_file_size(47_000_000)
    assert "~" not in host._selection_size_text(47_000_000, 40_000_000, 180.0)


def test_estimate_when_transcode_on():  # AC5
    host = _Host(checked=True, bitrate="320")
    # lossy remainder + lossless re-sized at 320 kbps
    expected = (47_000_000 - 40_000_000) + 180.0 * 320 * 1000 / 8
    assert host._selection_size_text(47_000_000, 40_000_000, 180.0) == (f"~{format_file_size(expected)} after conversion")


def test_higher_bitrate_gives_larger_estimate():  # AC6
    low = _Host(checked=True, bitrate="128")
    high = _Host(checked=True, bitrate="320")
    args = (47_000_000, 40_000_000, 180.0)

    def bytes_of(text):
        return float(text.split("~")[1].split()[0])

    assert bytes_of(high._selection_size_text(*args)) > bytes_of(low._selection_size_text(*args))


def test_no_lossless_tracks_stays_plain():  # AC8
    host = _Host(checked=True)
    assert host._selection_size_text(11_000_000, 0, 0.0) == format_file_size(11_000_000)
    assert "~" not in host._selection_size_text(11_000_000, 0, 0.0)


def test_disabled_checkbox_stays_plain():  # AC9
    host = _Host(checked=True, enabled=False)
    assert host._selection_size_text(47_000_000, 40_000_000, 180.0) == format_file_size(47_000_000)


def test_label_switches_form_when_checkbox_toggled():  # AC7
    host = _Host(checked=False, bitrate="320")
    host.add_item(size=47_000_000, lossless_size=40_000_000, lossless_duration=180.0)

    host._update_selected_items()
    assert host.track_count_label.text().endswith(format_file_size(47_000_000))

    host.transcode_mp3_check.setChecked(True)
    host._update_selected_items()
    expected = (47_000_000 - 40_000_000) + 180.0 * 320 * 1000 / 8
    assert host.track_count_label.text().endswith(f"~{format_file_size(expected)} after conversion")


class _LoaderStub(QObject):
    """Stand-in for SyncItemsLoader: no thread, emit on demand."""

    loaded = Signal(list, list)
    failed = Signal(str)

    def __init__(self, sync_manager):
        super().__init__()
        self.sync_manager = sync_manager
        self.running = False
        self.start_calls = 0

    def isRunning(self):
        return self.running

    def start(self):
        self.start_calls += 1
        self.running = True

    def finish(self, playlists, moods):
        self.running = False
        self.loaded.emit(playlists, moods)


class _TreeHost(SyncSelectionMixin):
    def __init__(self):
        self.sync_tree = SyncSelectionTree()
        self.track_count_label = QLabel()
        self.transcode_mp3_check = QCheckBox()
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["320"])
        self.sync_manager = Mock()
        self.sync_manager.selection_totals.return_value = (0, 0, 0, 0.0)
        self.current_profile = SimpleNamespace(playlist_ids=[], mood_ids=[])

    def _update_sync_button_state(self):
        pass


@pytest.fixture
def stub_loader(monkeypatch):
    created = []

    def factory(sync_manager):
        inst = _LoaderStub(sync_manager)
        created.append(inst)
        return inst

    monkeypatch.setattr(sync_selection_mixin, "SyncItemsLoader", factory)
    return created


def test_refresh_does_not_load_inline(stub_loader):  # perf-AC9
    host = _TreeHost()
    host._refresh_sync_items()

    host.sync_manager.get_playlists.assert_not_called()
    host.sync_manager.get_moods.assert_not_called()
    assert len(stub_loader) == 1 and stub_loader[0].start_calls == 1
    assert host.sync_tree.topLevelItemCount() == 0  # tree only fills on `loaded`

    stub_loader[0].finish([{"kind": "playlist", "playlist_id": 1, "name": "P", "track_count": 0}], [])
    labels = [host.sync_tree.topLevelItem(i).text(0) for i in range(host.sync_tree.topLevelItemCount())]
    assert labels == ["PLAYLISTS", "MOODS"]


def test_repeat_refresh_calls_are_coalesced(stub_loader):  # perf-AC10
    host = _TreeHost()
    for _ in range(5):
        host._refresh_sync_items()

    # One worker, started once; the rest just armed the trailing reload.
    assert len(stub_loader) == 1
    assert stub_loader[0].start_calls == 1
    assert host._sync_items_reload_pending is True

    stub_loader[0].finish([], [])
    # Completion consumes the pending flag with exactly one more load.
    assert len(stub_loader) == 2
    assert host._sync_items_reload_pending is False


def test_selection_reapplied_after_async_load(stub_loader):  # perf-AC12
    host = _TreeHost()
    host.current_profile = SimpleNamespace(playlist_ids=[7], mood_ids=[])
    host._refresh_sync_items()
    host._sync_items_loader.finish(
        [{"kind": "playlist", "playlist_id": 7, "name": "Kept", "track_count": 3, "size": 100}, {"kind": "playlist", "playlist_id": 8, "name": "Off", "track_count": 1, "size": 50}], []
    )

    checked = [it.data(0, Qt.UserRole)["playlist_id"] for it in host._iter_sync_items() if it.checkState(0) == Qt.Checked]
    assert checked == [7]
    assert "1 playlist " in host.track_count_label.text()  # _update_selected_items ran


def test_selection_summary_uses_deduped_manager_totals_not_a_per_item_sum(stub_loader):
    """
    Regression: the summary must reflect one deduped query over the checked
    playlist/mood ids, not the sum of each item's own aggregates (which
    double-counts a track shared by several selected items).
    """
    host = _TreeHost()
    host.sync_manager.selection_totals.return_value = (3, 7_000_000, 0, 0.0)
    host._refresh_sync_items()
    host._sync_items_loader.finish(
        [{"kind": "playlist", "playlist_id": 7, "name": "A", "track_count": 2, "size": 5_000_000}, {"kind": "playlist", "playlist_id": 8, "name": "B", "track_count": 2, "size": 5_000_000}],
        [{"kind": "mood", "mood_id": 3, "name": "M", "track_count": 2, "size": 5_000_000}],
    )
    for it in host._iter_sync_items():
        it.setCheckState(0, Qt.Checked)
    host._update_selected_items()

    host.sync_manager.selection_totals.assert_called_with([7, 8], [3])
    text = host.track_count_label.text()
    assert "3 tracks" in text  # from the manager, not the 6 a per-item sum gives
    assert text.endswith(format_file_size(7_000_000))  # not 15 MB
    assert "2 playlists" in text and "1 mood " in text


def test_failed_load_keeps_existing_tree(stub_loader):  # perf-AC14
    host = _TreeHost()
    host._refresh_sync_items()
    host._sync_items_loader.finish([{"kind": "playlist", "playlist_id": 1, "name": "P", "track_count": 0}], [])
    before = host.sync_tree.topLevelItemCount()

    host._refresh_sync_items()
    host._sync_items_loader.running = False
    host._sync_items_loader.failed.emit("db is locked")

    assert host.sync_tree.topLevelItemCount() == before  # tree untouched
    assert host._sync_items_reload_pending is False


def _names_under(tree_item):
    return [tree_item.child(i).text(0) for i in range(tree_item.childCount())]


def test_orphan_and_cycle_items_are_attached_at_top_level():
    host = _Host()
    header = host.sync_tree.add_section("PLAYLISTS")
    items = [
        {"kind": "playlist", "playlist_id": 1, "name": "Root", "parent_id": None},
        {"kind": "playlist", "playlist_id": 2, "name": "Orphan", "parent_id": 99},
        {"kind": "playlist", "playlist_id": 3, "name": "Orphan child", "parent_id": 2},
        {"kind": "playlist", "playlist_id": 4, "name": "Cycle A", "parent_id": 5},
        {"kind": "playlist", "playlist_id": 5, "name": "Cycle B", "parent_id": 4},
    ]

    host._add_hierarchy(header, items, "playlist_id")

    top = _names_under(header)
    assert {"Root", "Orphan", "Cycle A"} <= set(top)
    orphan = next(header.child(i) for i in range(header.childCount()) if header.child(i).text(0) == "Orphan")
    assert _names_under(orphan) == ["Orphan child"]
    assert len(list(host._iter_sync_items())) == 5


def test_selection_is_not_saved_before_the_tree_has_loaded():
    from src.sync.sync_profile import SyncProfile

    host = _Host()
    host.current_profile = SyncProfile(name="P", path="", playlist_ids=[7])
    host.profiles = [host.current_profile]
    host.profile_store = Mock()

    host._save_current_profile_selections()

    assert host.current_profile.playlist_ids == [7]
    host.profile_store.save.assert_not_called()

    host._populate_sync_tree([], [])
    host._save_current_profile_selections()
    assert host.current_profile.playlist_ids == []
    host.profile_store.save.assert_called_once()


# ---------------------------------------------------------------------------
# SyncSelectionTree: two-column checklist with a display-only "something inside
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SyncItemsLoader — runs get_playlists()/get_moods() off the GUI thread and
# ---------------------------------------------------------------------------


@pytest.fixture
def no_real_session_release(monkeypatch):
    release = Mock()
    monkeypatch.setattr(CancellableWorker, "_release_db_session", staticmethod(release))
    return release


def _collect(signal):
    seen = []
    signal.connect(lambda *a: seen.append(a))
    return seen


def test_run_emits_loaded_and_releases_session(no_real_session_release):  # perf-AC11
    manager = Mock()
    manager.get_playlists.return_value = [{"kind": "playlist", "playlist_id": 1}]
    manager.get_moods.return_value = [{"kind": "mood", "mood_id": 2}]
    loader = SyncItemsLoader(manager)
    got = _collect(loader.loaded)

    loader.run()
    QCoreApplication.processEvents()

    assert got == [([{"kind": "playlist", "playlist_id": 1}], [{"kind": "mood", "mood_id": 2}])]
    no_real_session_release.assert_called_once()


def test_run_emits_failed_and_releases_session_on_error(no_real_session_release):  # perf-AC11/AC14
    manager = Mock()
    manager.get_playlists.side_effect = RuntimeError("db is locked")
    loader = SyncItemsLoader(manager)
    failed = _collect(loader.failed)
    loaded = _collect(loader.loaded)

    loader.run()
    QCoreApplication.processEvents()

    assert loaded == []
    assert failed == [("db is locked",)]
    no_real_session_release.assert_called_once()


def test_cancelled_run_emits_nothing_but_still_releases(no_real_session_release):  # perf-AC11
    manager = Mock()
    manager.get_playlists.return_value = []
    manager.get_moods.return_value = []
    loader = SyncItemsLoader(manager)
    loaded = _collect(loader.loaded)
    loader.request_cancel()

    loader.run()
    QCoreApplication.processEvents()

    assert loaded == []
    no_real_session_release.assert_called_once()
