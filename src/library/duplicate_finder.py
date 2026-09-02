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

Two mutually-exclusive search modes (never blended):
  - "metadata"    (default): the title/artist/album/year string-similarity
                  scan described above.
  - "fingerprint": matches by AcoustID/chromaprint audio fingerprint instead
                  of tags entirely, catching duplicates that were mislabeled
                  or retitled. Blocks by a 4-second duration bucket instead
                  of by title, then compares fingerprints within each bucket
                  via a local, offline Hamming-distance comparison (no
                  AcoustID web service call). Only tracks with a fingerprint
                  already computed by the audio analysis pipeline
                  (audio_calculations.py) are eligible.

                  The comparison itself uses fingerprint_matching.py's
                  numpy-vectorized reimplementation of
                  acoustid.compare_fingerprints() (same algorithm, same
                  scores, ~40-90x faster per pair -- the reference pure-Python
                  version is too slow to make a process pool worthwhile),
                  spread across a ProcessPoolExecutor since it's CPU-bound
                  work that a single GIL-bound thread can't parallelize.

Architecture:
  - DuplicateScanWorker  : QThread — all comparison work off the UI thread
  - DuplicateFinderDialog: QDialog — UI opened from the File menu
"""

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from difflib import SequenceMatcher
import multiprocessing
import re
import time

import chromaprint
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QRadioButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.library.fingerprint_matching import score_fingerprint_batch
from src.statistics.batch_analysis_scheduler import recommended_worker_count
from src.track.base_track_view import BaseTrackView

# Pair count per process-pool task. Large enough to keep IPC/pickling
# overhead low relative to compute, small enough to keep progress reporting
# and cancellation reasonably responsive (a batch is the smallest unit of
# work that can't be interrupted mid-flight).
_FINGERPRINT_BATCH_SIZE = 2000

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
    # Some rows have a stray None entry in artist_roles (orphaned
    # association) — filter those out before searching.
    artist_roles = [ar for ar in (getattr(track, "artist_roles", None) or []) if ar]
    if artist_roles:
        primary = next(
            (ar for ar in artist_roles if getattr(ar.role, "role_name", "") == "Primary Artist"),
            None,
        )
        chosen = primary or artist_roles[0]
        return getattr(chosen.artist, "artist_name", "") or ""
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


class DuplicateScanWorker(CancellableWorker):
    """
    Background worker that finds duplicate tracks using a blocking strategy.

    Signals:
        progress(current, total)  - for the progress bar
        status(message)           - human-readable status string
        finished(groups, stopped_early) - list[list[track]], one inner list
                                    per group, plus whether the user stopped
                                    the scan before it finished
        error(message)
    """

    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(list, bool)  # groups, stopped_early
    error = Signal(str)

    def __init__(
        self,
        tracks: list,
        threshold: float,
        use_artist: bool,
        use_album: bool,
        use_year: bool,
        match_mode: str = "metadata",
        parent=None,
    ):
        super().__init__(parent)
        self._tracks = tracks
        self._threshold = threshold
        self._use_artist = use_artist
        self._use_album = use_album
        self._use_year = use_year
        self._match_mode = match_mode
        self._stopped_early = False

    def run(self):
        try:
            groups = self._find_duplicates()
            self.finished.emit(groups, self._stopped_early)
        except Exception as e:
            # Intentional broad boundary catch: this is a QThread's run() body
            # and must not let an exception kill the thread silently — report
            # it to the UI via the error signal instead.
            logger.error(f"DuplicateScanWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    # ------------------------------------------------------------------
    # Core algorithm
    # ------------------------------------------------------------------

    def _build_blocks(self) -> dict[str, list]:
        """
        Group tracks by blocking key. Only returns blocks with >= 2 tracks —
        single-track blocks can never produce a duplicate pair.

        "metadata" mode blocks by the first 3 chars of the normalised track
        name. "fingerprint" mode instead blocks tracks with a computed
        AcoustID fingerprint by a 4-second duration bucket -- true
        duplicates (same recording) have essentially identical durations,
        while mislabeled/retitled duplicates would never share a
        title-prefix block at all.
        """
        blocks: dict[str, list] = defaultdict(list)
        if self._match_mode == "fingerprint":
            for track in self._tracks:
                if not getattr(track, "acoustid_fingerprint", None):
                    continue
                duration = getattr(track, "duration", None)
                if not duration:
                    continue
                blocks[int(duration // 4)].append(track)
        else:
            for track in self._tracks:
                key = _blocking_key(getattr(track, "track_name", "") or "")
                if key:
                    blocks[key].append(track)
        return {k: v for k, v in blocks.items() if len(v) >= 2}

    def _decode_fingerprint(self, track) -> "np.ndarray | None":
        """Decode a track's stored chromaprint fingerprint into the int
        array fast_match_fingerprints() operates on. Returns None on failure
        (e.g. corrupt/legacy data) so the caller can drop the track from
        fingerprint-mode blocks entirely -- it could never score above 0.0
        against anything anyway.

        Fingerprints are stored as str (Track.acoustid_fingerprint is a Text
        column), but chromaprint.decode_fingerprint requires the raw bytes
        form originally returned by fingerprint_file() -- chromaprint
        fingerprints are plain-ASCII base64, so encode() round-trips exactly.
        """
        try:
            ints = chromaprint.decode_fingerprint(track.acoustid_fingerprint.encode())[0]
            return np.asarray(ints, dtype=np.uint32)
        except (chromaprint.FingerprintError, AttributeError) as e:
            logger.warning(
                f"Fingerprint decode failed for track {getattr(track, 'track_id', None)}: {e}"
            )
            return None

    def _score_pair_metadata(self, a, b) -> float:
        """
        Weighted similarity score for a pair of tracks.
        track_name always contributes (weight 2).
        Optional fields each have weight 1, and are skipped when either
        track has no value for that field.

        Two cheap, always-on signals guard against the classic false-positive
        case of classical music movements ("Symphony No. 5: I. Allegro" vs
        "...: II. Andante") which have near-identical track names but are
        clearly different tracks:
          - movement_number: if both tracks have one and they differ, the
            pair can never be a duplicate — short-circuit immediately.
          - duration: real duplicates of the same recording have very
            similar lengths; different movements/tracks usually don't.
        """
        mv_a = getattr(a, "movement_number", None)
        mv_b = getattr(b, "movement_number", None)
        if mv_a is not None and mv_b is not None and mv_a != mv_b:
            return 0.0

        name_score = _similarity(
            getattr(a, "track_name", "") or "", getattr(b, "track_name", "") or ""
        )
        weighted_sum = name_score * 2
        weight_total = 2.0

        dur_a = getattr(a, "duration", None)
        dur_b = getattr(b, "duration", None)
        if dur_a and dur_b:
            diff_ratio = abs(dur_a - dur_b) / max(dur_a, dur_b)
            weighted_sum += max(0.0, 1.0 - diff_ratio)
            weight_total += 1

        # Early exit: the remaining optional fields (artist/album/year) each
        # add at most weight 1. If even a perfect score on all of them
        # couldn't reach the threshold, skip the relationship lookups
        # (artist/album require walking ORM relationships) entirely. This is
        # what makes high thresholds noticeably faster than low ones.
        remaining = (
            (1 if self._use_artist else 0)
            + (1 if self._use_album else 0)
            + (1 if self._use_year else 0)
        )
        if remaining:
            best_possible = (weighted_sum + remaining) / (weight_total + remaining)
            if best_possible < self._threshold:
                return weighted_sum / weight_total

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

    def _find_duplicates_metadata(
        self, blocks: dict[str, list], track_index: dict[int, int], union, total_pairs: int
    ) -> tuple[int, bool]:
        """Single-threaded scan: string-similarity scoring is cheap enough
        (and already short-circuits via the best_possible check in
        _score_pair_metadata) that a process pool isn't worth the IPC
        overhead here."""
        checked = 0
        last_emitted = 0
        stopped = False

        for block_tracks in blocks.values():
            if self.is_cancelled:
                stopped = True
                break

            m = len(block_tracks)
            for i in range(m):
                if self.is_cancelled:
                    stopped = True
                    break
                for j in range(i + 1, m):
                    score = self._score_pair_metadata(block_tracks[i], block_tracks[j])
                    if score >= self._threshold:
                        union(track_index[id(block_tracks[i])], track_index[id(block_tracks[j])])
                    checked += 1
                    if checked - last_emitted >= 500:
                        self.progress.emit(checked, total_pairs)
                        last_emitted = checked
                        if self.is_cancelled:
                            stopped = True
                            break
                if stopped:
                    break
            if stopped:
                break

        return checked, stopped

    def _find_duplicates_fingerprint(
        self, blocks: dict[str, list], track_index: dict[int, int], union, total_pairs: int
    ) -> tuple[int, bool]:
        """Process-pool scan: fingerprint comparison is CPU-bound pure work
        (see fingerprint_matching.py) that benefits from real parallelism
        across cores -- a single GIL-bound thread can't get that.

        Each track's fingerprint is decoded once here (cheap, negligible
        cost) rather than per-pair. Candidate pairs are flattened across all
        blocks and chunked into fixed-size batches so IPC overhead stays low
        while progress/cancellation stay reasonably responsive; only decoded
        fingerprint arrays and integer pair indices cross the process
        boundary, never Qt/ORM objects.
        """
        decoded: dict[int, np.ndarray] = {}
        for block_tracks in blocks.values():
            for track in block_tracks:
                idx = track_index[id(track)]
                if idx in decoded:
                    continue
                array = self._decode_fingerprint(track)
                if array is not None:
                    decoded[idx] = array

        all_pairs: list[tuple[int, int]] = []
        for block_tracks in blocks.values():
            m = len(block_tracks)
            for i in range(m):
                idx_i = track_index[id(block_tracks[i])]
                if idx_i not in decoded:
                    continue
                for j in range(i + 1, m):
                    idx_j = track_index[id(block_tracks[j])]
                    if idx_j in decoded:
                        all_pairs.append((idx_i, idx_j))

        checked = 0
        stopped = False
        if not all_pairs:
            self.progress.emit(0, max(total_pairs, 1))
            return checked, stopped

        batches = [
            all_pairs[k : k + _FINGERPRINT_BATCH_SIZE]
            for k in range(0, len(all_pairs), _FINGERPRINT_BATCH_SIZE)
        ]
        num_workers = min(recommended_worker_count(), len(batches))
        last_emitted = 0

        logger.info(
            f"Fingerprint scan: {len(all_pairs):,} pairs -> {len(batches):,} "
            f"batches across {num_workers} worker process(es)"
        )

        # Forking a process that already has Qt/PySide6 initialised is a
        # known source of rare, hard-to-diagnose deadlocks (same rationale
        # as BatchAnalysisScheduler) -- use spawn explicitly rather than the
        # platform default.
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=num_workers, mp_context=ctx) as executor:
            pending = list(batches)
            in_flight: dict = {}

            def submit_more():
                while pending and len(in_flight) < num_workers * 2:
                    if self.is_cancelled:
                        return
                    batch = pending.pop(0)
                    referenced = {idx for pair in batch for idx in pair}
                    fp_subset = {idx: decoded[idx] for idx in referenced}
                    future = executor.submit(
                        score_fingerprint_batch, fp_subset, batch, self._threshold
                    )
                    in_flight[future] = len(batch)

            submit_more()
            while in_flight:
                done, _ = wait(in_flight.keys(), timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    batch_len = in_flight.pop(future)
                    for i, j in future.result():
                        union(i, j)
                    checked += batch_len
                    if checked - last_emitted >= 500 or checked == len(all_pairs):
                        self.progress.emit(checked, total_pairs)
                        last_emitted = checked

                if self.is_cancelled:
                    pending.clear()
                submit_more()

        stopped = checked < len(all_pairs)
        return checked, stopped

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
            f"Comparing {total_pairs:,} candidate pairs across {len(blocks):,} blocks..."
        )
        self.progress.emit(0, max(total_pairs, 1))

        # Union-Find keyed by object id() so we don't need a separate index dict
        track_index: dict[int, int] = {id(t): i for i, t in enumerate(self._tracks)}
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int):
            parent[find(x)] = find(y)

        if self._match_mode == "fingerprint":
            checked, stopped = self._find_duplicates_fingerprint(
                blocks, track_index, union, total_pairs
            )
        else:
            checked, stopped = self._find_duplicates_metadata(
                blocks, track_index, union, total_pairs
            )

        self._stopped_early = stopped
        if stopped:
            logger.info(f"Duplicate scan stopped by user after {checked:,} pairs")
        self.progress.emit(checked if stopped else total_pairs, total_pairs)

        # Collect groups of size >= 2
        buckets: dict[int, list] = defaultdict(list)
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
        self._groups: list[list] = []
        self._current_group_tracks: list = []
        self._scan_start_time: float | None = None

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
        outer = QVBoxLayout(group)
        outer.setSpacing(8)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        mode_row.addWidget(QLabel("Match by:"))

        self.mode_group = QButtonGroup(self)
        self.radio_metadata = QRadioButton("Metadata (title/artist/album)")
        self.radio_metadata.setChecked(True)
        self.radio_metadata.setToolTip(
            "Compare tags: track title, artist, album, year. Only takes effect on the next scan."
        )
        self.radio_fingerprint = QRadioButton("Audio Fingerprint")
        self.radio_fingerprint.setToolTip(
            "Compare actual audio content via AcoustID/chromaprint, "
            "regardless of tags -- catches mislabeled or retitled "
            "duplicates. Only tracks already analysed (Statistics > Audio "
            "Analysis) are eligible. Only takes effect on the next scan."
        )
        self.mode_group.addButton(self.radio_metadata)
        self.mode_group.addButton(self.radio_fingerprint)
        self.radio_metadata.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.radio_metadata)
        mode_row.addWidget(self.radio_fingerprint)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        layout = QHBoxLayout()
        layout.setSpacing(16)
        outer.addLayout(layout)

        self.compare_fields_label = QLabel("Compare fields:")
        layout.addWidget(self.compare_fields_label)

        self.chk_artist = QCheckBox("Artist")
        self.chk_artist.setChecked(True)
        self.chk_artist.setToolTip(
            "Include primary artist in similarity scoring. Only takes effect "
            "on the next scan — click Scan Library to apply."
        )
        self.chk_artist.toggled.connect(self._on_settings_changed)
        layout.addWidget(self.chk_artist)

        self.chk_album = QCheckBox("Album")
        self.chk_album.setChecked(False)
        self.chk_album.setToolTip(
            "Include album name — useful if the same song appears on "
            "multiple albums and you want to keep both. Only takes effect "
            "on the next scan — click Scan Library to apply."
        )
        self.chk_album.toggled.connect(self._on_settings_changed)
        layout.addWidget(self.chk_album)

        self.chk_year = QCheckBox("Year")
        self.chk_year.setChecked(False)
        self.chk_year.setToolTip(
            "Require matching release year — reduces false positives "
            "for covers and remasters. Only takes effect on the next scan "
            "— click Scan Library to apply."
        )
        self.chk_year.toggled.connect(self._on_settings_changed)
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
        self.threshold_slider.valueChanged.connect(self._on_settings_changed)
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
        self.group_title_label.setProperty("title", True)
        right_layout.addWidget(self.group_title_label)

        self.track_view = BaseTrackView(
            controller=self.controller, tracks=[], title="", enable_drag=False, enable_drop=False
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
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Could not load tracks:\n{e}")
            return

        if not all_tracks:
            show_status_message(self, "No tracks found in library.")
            return

        self._groups = []
        self.group_list.clear()
        self.track_view.load_data([])
        self.group_title_label.setText("Scan in progress...")
        self.status_label.setText(f"Preparing to scan {len(all_tracks):,} tracks...")
        self._scan_start_time = time.monotonic()

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
            match_mode="metadata" if self.radio_metadata.isChecked() else "fingerprint",
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.request_cancel()
        self.stop_button.setEnabled(False)
        self.status_label.setText("Stopping...")

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

        eta = self._estimate_remaining(current, total)
        if eta:
            self.progress_bar.setFormat(f"Comparing pairs: {current:,} / {total:,}  (ETA: {eta})")
        else:
            self.progress_bar.setFormat(f"Comparing pairs: {current:,} / {total:,}")

    def _estimate_remaining(self, current: int, total: int) -> str | None:
        """Return a human-readable ETA string, or None if not enough data yet."""
        if not self._scan_start_time or current <= 0 or current >= total:
            return None

        elapsed = time.monotonic() - self._scan_start_time
        if elapsed < 1.0:
            return None

        rate = current / elapsed
        if rate <= 0:
            return None

        remaining_seconds = int((total - current) / rate)
        if remaining_seconds < 60:
            return f"{remaining_seconds}s"
        minutes, seconds = divmod(remaining_seconds, 60)
        return f"{minutes}m {seconds:02d}s"

    def _on_scan_finished(self, groups: list, stopped_early: bool = False):
        self._groups = groups
        self.progress_bar.hide()
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._populate_group_list()

        prefix = "Stopped early - partial results. " if stopped_early else ""

        if not groups:
            self.status_label.setText(f"{prefix}No duplicates found with the current settings.")
            self.group_title_label.setText("No duplicate groups found.")
        else:
            total_tracks = sum(len(g) for g in groups)
            self.status_label.setText(
                f"{prefix}Found {len(groups):,} duplicate group(s) involving "
                f"{total_tracks:,} tracks."
            )
            self.group_title_label.setText("Select a group on the left to inspect tracks.")

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
            item = QListWidgetItem(f"Group {i + 1}  -  {len(group)} tracks\n{name} - {artist}")
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
            self.group_title_label.setText("All groups resolved - no duplicates remain.")
            self.track_view.load_data([])
        else:
            self.group_list.setCurrentRow(min(current_row, new_count - 1))

        total_tracks = sum(len(g) for g in self._groups)
        self.status_label.setText(
            f"{len(self._groups):,} group(s) remaining, {total_tracks:,} tracks involved."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_mode_changed(self, metadata_checked: bool):
        """Toggling match mode disables the fields that don't apply to the
        other mode and resets the threshold to a mode-appropriate default --
        AcoustID similarity scores cluster much closer to 1.0 for true
        duplicates than title-similarity scores do."""
        self.compare_fields_label.setEnabled(metadata_checked)
        self.chk_artist.setEnabled(metadata_checked)
        self.chk_album.setEnabled(metadata_checked)
        self.chk_year.setEnabled(metadata_checked)
        self.threshold_slider.setValue(85 if metadata_checked else 90)
        self._on_settings_changed()

    def _on_threshold_changed(self, value: int):
        self.threshold_label.setText(f"{value}%")

    def _on_settings_changed(self):
        """Remind the user that scan settings only apply on the next scan."""
        if self._worker and self._worker.isRunning():
            return
        if self._groups:
            self.status_label.setText("Settings changed — click Scan Library to apply them.")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(3000)
        super().closeEvent(event)
