# duplicate_finder.py
"""
Duplicate Track Finder
======================
Finds tracks in the library that are likely duplicates of each other using
string similarity on track metadata fields.

Performance strategy — "blocking":
  Instead of comparing every track against every other track (O(n²) = 1.3 billion
  pairs for 51k tracks), we first group tracks into "blocks" by a cheap key derived
  from the track name. We only run the expensive similarity comparison *within*
  each block. This cuts the number of comparisons by ~99% while still catching
  genuine duplicates.

  Blocking key = first 3 characters of the normalised track name (lowercased,
  punctuation stripped). Tracks that can't possibly match (e.g. "Bohemian Rhapsody"
  vs "Stairway to Heaven") never get compared at all.

Architecture:
  - DuplicateScanWorker  : QThread — all comparison work off the UI thread
  - DuplicateFinderDialog: QDialog — UI opened from the File menu
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Dict, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.base_track_view import BaseTrackView
from src.logger_config import logger

# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = _PUNCT_RE.sub("", text)
    return " ".join(text.split())


def _blocking_key(track_name: str) -> str:
    """
    Cheap key used to decide which tracks are worth comparing.
    Returns the first 3 characters of the normalised name.
    This groups "bohemian rhapsody", "bohemia" etc. into the same block
    while keeping "stairway to heaven" completely separate.
    """
    norm = _normalise(track_name)
    return norm[:3] if norm else ""


def _similarity(a: str, b: str) -> float:
    """0.0-1.0 similarity between two normalised strings."""
    a = _normalise(a)
    b = _normalise(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _get_primary_artist_string(track) -> str:
    """Pull the primary artist display string from a track ORM object."""
    val = getattr(track, "primary_artist_names", None)
    if val and val != "Unknown Artist":
        return str(val)
    artist_roles = getattr(track, "artist_roles", None)
    if artist_roles:
        primary = next(
            (
                ar
                for ar in artist_roles
                if getattr(ar.role, "role_name", "") == "Primary Artist"
            ),
            None,
        )
        if primary:
            return getattr(primary.artist, "artist_name", "") or ""
        return getattr(artist_roles[0].artist, "artist_name", "") or ""
    return ""


def _get_album_string(track) -> str:
    """Pull album name from a track ORM object."""
    album = getattr(track, "album", None)
    if album:
        return getattr(album, "album_name", "") or ""
    return getattr(track, "album_name", "") or ""


# ---------------------------------------------------------------------------
# DuplicateScanWorker
# ---------------------------------------------------------------------------


class DuplicateScanWorker(QThread):
    """
    Background worker that finds duplicate tracks using a blocking strategy.

    Signals:
        progress(current, total)  - for the progress bar
        status(message)           - human-readable status string
        finished(groups)          - list[list[track]], one inner list per group
        error(message)
    """

    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        tracks: list,
        threshold: float,
        use_artist: bool,
        use_album: bool,
        use_year: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._tracks = tracks
        self._threshold = threshold
        self._use_artist = use_artist
        self._use_album = use_album
        self._use_year = use_year
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            groups = self._find_duplicates()
            self.finished.emit(groups)
        except Exception as e:
            logger.error(f"DuplicateScanWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    # ------------------------------------------------------------------
    # Core algorithm
    # ------------------------------------------------------------------

    def _build_blocks(self) -> Dict[str, list]:
        """
        Group tracks by blocking key (first 3 chars of normalised name).
        Only returns blocks with >= 2 tracks — single-track blocks can
        never produce a duplicate pair.
        """
        blocks: Dict[str, list] = defaultdict(list)
        for track in self._tracks:
            key = _blocking_key(getattr(track, "track_name", "") or "")
            if key:
                blocks[key].append(track)
        return {k: v for k, v in blocks.items() if len(v) >= 2}

    def _score_pair(self, a, b) -> float:
        """
        Weighted similarity score for a pair of tracks.
        track_name always contributes (weight 2).
        Optional fields each have weight 1, and are skipped when either
        track has no value for that field.
        """
        weighted_sum = 0.0
        weight_total = 0.0

        name_score = _similarity(
            getattr(a, "track_name", "") or "",
            getattr(b, "track_name", "") or "",
        )
        weighted_sum += name_score * 2
        weight_total += 2

        if self._use_artist:
            sa, sb = _get_primary_artist_string(a), _get_primary_artist_string(b)
            if sa and sb:
                weighted_sum += _similarity(sa, sb)
                weight_total += 1

        if self._use_album:
            aa, ab = _get_album_string(a), _get_album_string(b)
            if aa and ab:
                weighted_sum += _similarity(aa, ab)
                weight_total += 1

        if self._use_year:
            ya = str(getattr(a, "release_year", "") or "")
            yb = str(getattr(b, "release_year", "") or "")
            if ya and yb:
                weighted_sum += 1.0 if ya == yb else 0.0
                weight_total += 1

        return weighted_sum / weight_total if weight_total else 0.0

    def _find_duplicates(self) -> list:
        """
        Main routine:
          1. Build blocks (fast single pass)
          2. Compare pairs only within each block
          3. Union-find merges pairs into groups
          4. Return groups of size >= 2
        """
        n = len(self._tracks)
        self.status.emit(f"Building candidate blocks from {n:,} tracks...")
        self.progress.emit(0, 1)

        blocks = self._build_blocks()
        total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in blocks.values())

        logger.info(
            f"Blocking: {n:,} tracks -> {len(blocks):,} blocks -> "
            f"{total_pairs:,} pairs (was {n * (n - 1) // 2:,} without blocking)"
        )
        self.status.emit(
            f"Comparing {total_pairs:,} candidate pairs across "
            f"{len(blocks):,} blocks..."
        )
        self.progress.emit(0, max(total_pairs, 1))

        # Union-Find keyed by object id() so we don't need a separate index dict
        track_index: Dict[int, int] = {id(t): i for i, t in enumerate(self._tracks)}
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int):
            parent[find(x)] = find(y)

        checked = 0
        last_emitted = 0

        for block_tracks in blocks.values():
            if self._stop_requested:
                logger.info("Duplicate scan stopped by user")
                break

            m = len(block_tracks)
            for i in range(m):
                for j in range(i + 1, m):
                    score = self._score_pair(block_tracks[i], block_tracks[j])
                    if score >= self._threshold:
                        union(
                            track_index[id(block_tracks[i])],
                            track_index[id(block_tracks[j])],
                        )
                    checked += 1
                    if checked - last_emitted >= 500:
                        self.progress.emit(checked, total_pairs)
                        last_emitted = checked

        self.progress.emit(total_pairs, total_pairs)

        # Collect groups of size >= 2
        buckets: Dict[int, list] = defaultdict(list)
        for idx, track in enumerate(self._tracks):
            buckets[find(idx)].append(track)

        groups = [m for m in buckets.values() if len(m) >= 2]
        groups.sort(key=lambda g: len(g), reverse=True)

        logger.info(f"Duplicate scan complete: {len(groups)} group(s) found")
        return groups


# ---------------------------------------------------------------------------
# DuplicateFinderDialog
# ---------------------------------------------------------------------------


class DuplicateFinderDialog(QDialog):
    """
    Main dialog for finding and reviewing duplicate tracks.

    Layout:
      Settings bar  ->  progress bar  ->  splitter (group list | track view)
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._worker: DuplicateScanWorker | None = None
        self._groups: List[List] = []
        self._current_group_tracks: List = []

        self.setWindowTitle("Duplicate Track Finder")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 750)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._build_settings_group())

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("Configure options above and click Scan Library.")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_label)

        root.addWidget(self._build_splitter(), stretch=1)

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("Scan Settings")
        layout = QHBoxLayout(group)
        layout.setSpacing(16)

        layout.addWidget(QLabel("Compare fields:"))

        self.chk_artist = QCheckBox("Artist")
        self.chk_artist.setChecked(True)
        self.chk_artist.setToolTip("Include primary artist in similarity scoring")
        layout.addWidget(self.chk_artist)

        self.chk_album = QCheckBox("Album")
        self.chk_album.setChecked(False)
        self.chk_album.setToolTip(
            "Include album name — useful if the same song appears on "
            "multiple albums and you want to keep both"
        )
        layout.addWidget(self.chk_album)

        self.chk_year = QCheckBox("Year")
        self.chk_year.setChecked(False)
        self.chk_year.setToolTip(
            "Require matching release year — reduces false positives "
            "for covers and remasters"
        )
        layout.addWidget(self.chk_year)

        layout.addSpacing(16)
        layout.addWidget(QLabel("Similarity threshold:"))

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(50, 100)
        self.threshold_slider.setValue(85)
        self.threshold_slider.setTickInterval(5)
        self.threshold_slider.setTickPosition(QSlider.TicksBelow)
        self.threshold_slider.setFixedWidth(180)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        layout.addWidget(self.threshold_slider)

        self.threshold_label = QLabel("85%")
        self.threshold_label.setFixedWidth(36)
        layout.addWidget(self.threshold_label)

        layout.addSpacing(16)

        self.scan_button = QPushButton("Scan Library")
        self.scan_button.clicked.connect(self._start_scan)
        layout.addWidget(self.scan_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_scan)
        layout.addWidget(self.stop_button)

        layout.addStretch()
        return group

    def _build_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)

        # Left: group list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Duplicate Groups:"))

        self.group_list = QListWidget()
        self.group_list.setAlternatingRowColors(True)
        self.group_list.currentRowChanged.connect(self._on_group_selected)
        self.group_list.setMinimumWidth(260)
        self.group_list.setMaximumWidth(380)
        left_layout.addWidget(self.group_list)
        splitter.addWidget(left)

        # Right: BaseTrackView for the selected group
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.group_title_label = QLabel("Select a group on the left to inspect tracks.")
        self.group_title_label.setStyleSheet("font-weight: bold; padding: 4px;")
        right_layout.addWidget(self.group_title_label)

        self.track_view = BaseTrackView(
            controller=self.controller,
            tracks=[],
            title="",
            enable_drag=False,
            enable_drop=False,
        )
        # Embed as a widget, not a floating window
        self.track_view.setWindowFlags(Qt.Widget)
        self.track_view.setMinimumWidth(600)
        # Refresh group list when user deletes a track via the context menu
        self.track_view.track_deleted.connect(self._on_track_deleted)

        right_layout.addWidget(self.track_view)
        splitter.addWidget(right)

        splitter.setSizes([300, 900])
        return splitter

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return

        try:
            all_tracks = self.controller.get.get_all_entities("Track")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load tracks:\n{e}")
            return

        if not all_tracks:
            QMessageBox.information(self, "No Tracks", "No tracks found in library.")
            return

        self._groups = []
        self.group_list.clear()
        self.track_view.load_data([])
        self.group_title_label.setText("Scan in progress...")
        self.status_label.setText(f"Preparing to scan {len(all_tracks):,} tracks...")

        # Indeterminate bar while building blocks
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.scan_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self._worker = DuplicateScanWorker(
            tracks=all_tracks,
            threshold=self.threshold_slider.value() / 100.0,
            use_artist=self.chk_artist.isChecked(),
            use_album=self.chk_album.isChecked(),
            use_year=self.chk_year.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.stop()
        self.stop_button.setEnabled(False)
        self.status_label.setText("Stopping...")

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"Comparing pairs: {current:,} / {total:,}")

    def _on_scan_finished(self, groups: list):
        self._groups = groups
        self.progress_bar.hide()
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._populate_group_list()

        if not groups:
            self.status_label.setText("No duplicates found with the current settings.")
            self.group_title_label.setText("No duplicate groups found.")
        else:
            total_tracks = sum(len(g) for g in groups)
            self.status_label.setText(
                f"Found {len(groups):,} duplicate group(s) involving "
                f"{total_tracks:,} tracks."
            )
            self.group_title_label.setText(
                "Select a group on the left to inspect tracks."
            )

    def _on_scan_error(self, message: str):
        self.progress_bar.hide()
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText(f"Scan error: {message}")
        QMessageBox.critical(self, "Scan Error", f"The scan failed:\n{message}")

    # ------------------------------------------------------------------
    # Group list
    # ------------------------------------------------------------------

    def _populate_group_list(self):
        self.group_list.clear()
        for i, group in enumerate(self._groups):
            first = group[0]
            name = getattr(first, "track_name", "Unknown") or "Unknown"
            artist = _get_primary_artist_string(first) or "Unknown Artist"
            item = QListWidgetItem(
                f"Group {i + 1}  -  {len(group)} tracks\n{name} - {artist}"
            )
            item.setData(Qt.UserRole, i)
            self.group_list.addItem(item)

    def _on_group_selected(self, row: int):
        if row < 0 or row >= len(self._groups):
            return
        group = self._groups[row]
        self._current_group_tracks = list(group)
        first = group[0]
        name = getattr(first, "track_name", "Unknown") or "Unknown"
        self.group_title_label.setText(
            f"Group {row + 1} - {len(group)} possible duplicates of '{name}'"
        )
        self.track_view.load_data(self._current_group_tracks)

    # ------------------------------------------------------------------
    # Live refresh after deletion
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int):
        """Remove the deleted track from its group and refresh the list."""
        new_groups = []
        for group in self._groups:
            updated = [t for t in group if t.track_id != track_id]
            if len(updated) >= 2:
                new_groups.append(updated)
        self._groups = new_groups

        current_row = self.group_list.currentRow()
        self._populate_group_list()

        new_count = self.group_list.count()
        if new_count == 0:
            self.group_title_label.setText(
                "All groups resolved - no duplicates remain."
            )
            self.track_view.load_data([])
        else:
            self.group_list.setCurrentRow(min(current_row, new_count - 1))

        total_tracks = sum(len(g) for g in self._groups)
        self.status_label.setText(
            f"{len(self._groups):,} group(s) remaining, "
            f"{total_tracks:,} tracks involved."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_threshold_changed(self, value: int):
        self.threshold_label.setText(f"{value}%")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        super().closeEvent(event)
