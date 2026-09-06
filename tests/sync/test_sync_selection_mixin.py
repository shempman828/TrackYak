"""
The Sync view's selection tree/summary mixin.

- The selection summary (`N playlist(s) · N tracks · <size>`): with "Convert
  lossless files to MP3" on, the size becomes a post-conversion estimate
  (lossy tracks unchanged, lossless tracks re-sized as CBR MP3 at the chosen
  bitrate).
- `_refresh_sync_items()` loads playlists/moods off the GUI thread and
  coalesces repeat calls (it fires on every showEvent).
"""

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QTreeWidget, QTreeWidgetItem
import pytest

from src.sync import sync_selection_mixin
from src.sync.device_card import format_file_size
from src.sync.sync_selection_mixin import SyncSelectionMixin

pytestmark = pytest.mark.usefixtures("qapp")


class _Host(SyncSelectionMixin):
    """Minimal carrier for the mixin: real widgets, no full SyncView."""

    def __init__(self, *, checked=False, enabled=True, bitrate="320"):
        self.sync_tree = QTreeWidget()
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
        item = QTreeWidgetItem(self.sync_tree, ["x"])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        item.setData(0, Qt.UserRole, data)
        return item


# --- _selection_size_text -------------------------------------------------


def test_plain_size_when_transcode_off():  # AC4
    host = _Host(checked=False)
    assert host._selection_size_text(47_000_000, 40_000_000, 180.0) == format_file_size(47_000_000)
    assert "~" not in host._selection_size_text(47_000_000, 40_000_000, 180.0)


def test_estimate_when_transcode_on():  # AC5
    host = _Host(checked=True, bitrate="320")
    # lossy remainder + lossless re-sized at 320 kbps
    expected = (47_000_000 - 40_000_000) + 180.0 * 320 * 1000 / 8
    assert host._selection_size_text(47_000_000, 40_000_000, 180.0) == (
        f"~{format_file_size(expected)} after conversion"
    )


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


# --- full label via _update_selected_items -------------------------------


def test_label_switches_form_when_checkbox_toggled():  # AC7
    host = _Host(checked=False, bitrate="320")
    host.add_item(size=47_000_000, lossless_size=40_000_000, lossless_duration=180.0)

    host._update_selected_items()
    assert host.track_count_label.text().endswith(format_file_size(47_000_000))

    host.transcode_mp3_check.setChecked(True)
    host._update_selected_items()
    expected = (47_000_000 - 40_000_000) + 180.0 * 320 * 1000 / 8
    assert host.track_count_label.text().endswith(f"~{format_file_size(expected)} after conversion")


# --- async _refresh_sync_items (Slice C) --------------------------------


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
        self.sync_tree = QTreeWidget()
        self.track_count_label = QLabel()
        self.transcode_mp3_check = QCheckBox()
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["320"])
        self.sync_manager = Mock()
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

    stub_loader[0].finish(
        [{"kind": "playlist", "playlist_id": 1, "name": "P", "track_count": 0}], []
    )
    labels = [
        host.sync_tree.topLevelItem(i).text(0) for i in range(host.sync_tree.topLevelItemCount())
    ]
    assert labels == ["PLAYLISTS  (1)", "MOODS  (0)"]


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
        [
            {"kind": "playlist", "playlist_id": 7, "name": "Kept", "track_count": 3, "size": 100},
            {"kind": "playlist", "playlist_id": 8, "name": "Off", "track_count": 1, "size": 50},
        ],
        [],
    )

    checked = [
        it.data(0, Qt.UserRole)["playlist_id"]
        for it in host._iter_sync_items()
        if it.checkState(0) == Qt.Checked
    ]
    assert checked == [7]
    assert "1 playlist(s)" in host.track_count_label.text()  # _update_selected_items ran


def test_failed_load_keeps_existing_tree(stub_loader):  # perf-AC14
    host = _TreeHost()
    host._refresh_sync_items()
    host._sync_items_loader.finish(
        [{"kind": "playlist", "playlist_id": 1, "name": "P", "track_count": 0}], []
    )
    before = host.sync_tree.topLevelItemCount()

    host._refresh_sync_items()
    host._sync_items_loader.running = False
    host._sync_items_loader.failed.emit("db is locked")

    assert host.sync_tree.topLevelItemCount() == before  # tree untouched
    assert host._sync_items_reload_pending is False
