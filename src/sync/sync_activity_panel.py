"""
SyncActivityPanel -- the Sync view's Activity page.

Presents a sync run as a result, not a text dump:

    headline            "Synced to Pixel 8" / "Syncing to Pixel 8…"
    stat tiles          Copied · Skipped · Converted · Failed · Removed
    results tree        one row per playlist/mood (✓ / ✗ + counts); its
                        failures are child rows. Removed files get a row too.
    Details (collapsed) the plain-text log, `sync_log`, for copying out.

SyncExecutionMixin drives it through begin() / add_result() / add_removed()
/ finish(); it still writes the text log itself via `sync_log`.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStackedWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from src.common.widgets.item_foreground_delegate import ItemForegroundDelegate
from src.common.widgets.style_utils import set_style_property

# Failures under one result row are expanded automatically up to this many;
# a longer list stays collapsed so it doesn't push the other rows away.
_AUTO_EXPAND_FAILURES = 10

# Theme green / pink / dim (dark_mode.qss palette); QSS can't reach tree items.
_OK = QColor("#99EA85")
_ERROR = QColor("#EA8599")
_MUTED = QColor("#555e7a")

_STATS = [
    ("copied", "Copied"),
    ("skipped", "Skipped"),
    ("transcoded", "Converted"),
    ("failed", "Failed"),
    ("removed", "Removed"),
]


class _StatTile(QFrame):
    """A big number over a small caption."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SyncStatTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)
        self.value_label = QLabel("0")
        self.value_label.setObjectName("SyncStatValue")
        caption_label = QLabel(caption.upper())
        caption_label.setObjectName("SyncStatCaption")
        layout.addWidget(self.value_label)
        layout.addWidget(caption_label)

    def set_value(self, value: int, tone: str | None = None):
        self.value_label.setText(f"{value:,}")
        set_style_property(self, "tone", tone if value else None)


class SyncActivityPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._totals = dict.fromkeys((key for key, _ in _STATS), 0)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # -- empty state -------------------------------------------------------
        empty = QLabel("No sync has run yet.\nStart one with “Sync now” in the bar below.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setProperty("textRole", "placeholder")
        self._stack.addWidget(empty)

        # -- run view ------------------------------------------------------------
        run = QWidget()
        layout = QVBoxLayout(run)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        head_row = QHBoxLayout()
        self.headline = QLabel()
        self.headline.setObjectName("SyncActivityHeadline")
        head_row.addWidget(self.headline, 1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setProperty("linkButton", True)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear)
        head_row.addWidget(self.clear_btn)
        layout.addLayout(head_row)

        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        self._tiles: dict[str, _StatTile] = {}
        for key, caption in _STATS:
            tile = _StatTile(caption)
            self._tiles[key] = tile
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        self.results_tree = QTreeWidget()
        self.results_tree.setObjectName("SyncResultsTree")
        self.results_tree.setColumnCount(2)
        self.results_tree.setHeaderHidden(True)
        self.results_tree.setRootIsDecorated(True)
        self.results_tree.setItemDelegate(ItemForegroundDelegate(self.results_tree))
        header = self.results_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.results_tree, 1)

        self.details_btn = QPushButton("Show log ▸")
        self.details_btn.setProperty("linkButton", True)
        self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.setCheckable(True)
        self.details_btn.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_btn, 0, Qt.AlignLeft)

        self.sync_log = QTextEdit()
        self.sync_log.setObjectName("SyncLogView")
        self.sync_log.setReadOnly(True)
        self.sync_log.setVisible(False)
        layout.addWidget(self.sync_log, 1)

        self._stack.addWidget(run)

    # -- driven by SyncExecutionMixin -------------------------------------------

    def begin(self, destination: str):
        self._totals = dict.fromkeys(self._totals, 0)
        for tile in self._tiles.values():
            tile.set_value(0)
        self.results_tree.clear()
        self.headline.setText(f"Syncing to {destination}…")
        set_style_property(self.headline, "tone", None)
        self.clear_btn.setEnabled(False)
        self._stack.setCurrentIndex(1)

    def add_result(self, result: dict):
        ok = result.get("success", False)
        copied = result.get("tracks_copied", 0)
        skipped = result.get("tracks_skipped", 0)
        failed = result.get("tracks_failed", 0)
        transcoded = result.get("tracks_transcoded", 0)

        notes = [f"{copied:,} copied"]
        if skipped:
            notes.append(f"{skipped:,} already there")
        if transcoded:
            notes.append(f"{transcoded:,} to MP3")
        if failed:
            notes.append(f"{failed:,} failed")
        detail = "  ·  ".join(notes) if ok or copied else result.get("message", "")

        row = QTreeWidgetItem(
            self.results_tree, [f"{'✓' if ok else '✗'}  {result['playlist_name']}", detail]
        )
        row.setToolTip(0, result.get("message", ""))
        row.setData(0, Qt.UserRole, "ok" if ok and not failed else "error")
        failures = result.get("failures", [])
        for failure in failures:
            child = QTreeWidgetItem(row, [f"{failure['artist']} — {failure['title']}", failure["reason"]])
            child.setToolTip(0, child.text(0))
            child.setForeground(1, _MUTED)
        row.setExpanded(0 < len(failures) <= _AUTO_EXPAND_FAILURES)
        self._paint_row(row)

        for key, value in (
            ("copied", copied),
            ("skipped", skipped),
            ("failed", failed),
            ("transcoded", transcoded),
        ):
            self._totals[key] += value
        self._refresh_tiles()
        self.results_tree.scrollToItem(row)

    def add_removed(self, names: list[str]):
        if not names:
            return
        row = QTreeWidgetItem(
            self.results_tree,
            ["🗑  Removed from destination", f"{len(names):,} file{'' if len(names) == 1 else 's'}"],
        )
        for name in names:
            QTreeWidgetItem(row, [name, ""])
        row.setForeground(1, _MUTED)
        self._totals["removed"] = len(names)
        self._refresh_tiles()

    def finish(self, headline: str, tone: str):
        """tone: "ok" | "warn" | "error"."""
        self.headline.setText(headline)
        set_style_property(self.headline, "tone", tone)
        self.clear_btn.setEnabled(True)

    def clear(self):
        self.results_tree.clear()
        self.sync_log.clear()
        self._stack.setCurrentIndex(0)

    # -- internals ---------------------------------------------------------------

    def _refresh_tiles(self):
        tones = {"copied": "ok", "failed": "error", "removed": "warn"}
        for key, tile in self._tiles.items():
            tile.set_value(self._totals[key], tones.get(key))

    @staticmethod
    def _paint_row(row: QTreeWidgetItem):
        row.setForeground(0, _OK if row.data(0, Qt.UserRole) == "ok" else _ERROR)
        row.setForeground(1, _MUTED)

    def _toggle_details(self, shown: bool):
        self.sync_log.setVisible(shown)
        self.details_btn.setText("Hide log ▾" if shown else "Show log ▸")
