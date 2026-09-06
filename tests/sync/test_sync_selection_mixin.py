"""
The Sync view's selection summary (`N playlist(s) · N tracks · <size>`).

When "Convert lossless files to MP3" is on, the size switches from the raw
library footprint to a post-conversion estimate: lossy tracks unchanged,
lossless tracks re-sized as CBR MP3 at the chosen bitrate.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QTreeWidget, QTreeWidgetItem
import pytest

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
