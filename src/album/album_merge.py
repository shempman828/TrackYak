from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import ClassVar

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.common.dialogs.base_merge_dialog import MergeDBDialog
from src.common.dismissed_duplicates import DEFAULT_DISMISSED_DUPLICATES_PATH, dismiss_pair, load_dismissed_pairs, normalize_pair
from src.foundation.logger_config import logger

# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------


def _tokenset(text: str) -> set:
    """Lowercase, strip punctuation, return set of tokens."""
    if not text:
        return set()
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return set(text.split())


# Parenthetical/bracketed tags that describe a repackaging of the same
# underlying release rather than a different album. Stripped before
# comparison so two unrelated albums that happen to share a tag (e.g. two
# different movie soundtracks) aren't scored as similar just because of it,
# while an album that only differs from another by one of these tags (e.g.
# 'Abbey Road' vs 'Abbey Road (Remastered)') is recognised as the same
# release.
_EDITION_TAG_RE = re.compile(
    r"[\(\[][^\)\]]*\b("
    r"deluxe|remaster(?:ed)?|expanded(?:\s+edition)?|anniversary(?:\s+edition)?|"
    r"special\s+edition|bonus\s+tracks?(?:\s+version)?|collector'?s\s+edition|"
    r"limited\s+edition|reissue|explicit|clean(?:\s+version)?|"
    r"mono(?:\s+version)?|stereo(?:\s+version)?|"
    r"(?:original\s+motion\s+picture\s+)?soundtrack"
    r")\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Series markers ('Vol. 1', 'Part 2', 'Disc 3', ...) — a strong signal two
# albums are *different* entries in the same series, even when the rest of
# the title matches closely.
_VOLUME_RE = re.compile(r"\b(?:vol(?:ume)?s?|pt|part|disc|disk|book)\.?\s*(\d+)\b", re.IGNORECASE)

_UNKNOWN_ARTIST_NAMES = {"unknown artist", "unknown", "various", "various artists"}


def _strip_edition_tags(text: str) -> str:
    """Remove edition/soundtrack tags that don't distinguish one release
    from another (deluxe, remastered, original motion picture soundtrack...)."""
    return _EDITION_TAG_RE.sub("", text).strip()


def _volume_number(text: str):
    """Extract a series/volume number, e.g. 'Vol. 2' -> '2', else None."""
    if not text:
        return None
    m = _VOLUME_RE.search(text)
    return m.group(1) if m else None


def _name_similarity(a: str, b: str) -> float:
    """Substring-aware similarity between two strings, edition-tag- and volume-number-aware."""
    # Uses SequenceMatcher as the base score but boosts when one name is
    # contained within the other (handles 'Swings with Billy Mays' vs
    # 'Frank Sinatra Swings with Billy Mays'). Edition/soundtrack tags are
    # stripped before comparing so shared boilerplate doesn't inflate the
    # score of otherwise-unrelated albums, and titles identical once such a
    # tag is removed are treated as a near-certain match. Differing
    # volume/part/disc numbers cap the score, since those mark distinct
    # entries in a series rather than duplicates.
    if not a or not b:
        return 0.0
    al, bl = a.lower().strip(), b.lower().strip()
    if al == bl:
        return 1.0

    core_a = _strip_edition_tags(al)
    core_b = _strip_edition_tags(bl)

    if core_a and core_a == core_b:
        return 0.97

    base = SequenceMatcher(None, core_a, core_b).ratio()

    # Containment boost — one name is a substring of the other
    if core_a in core_b or core_b in core_a:
        base = min(base + 0.25, 1.0)

    # Token overlap boost
    ta, tb = _tokenset(core_a), _tokenset(core_b)
    if ta and tb:
        overlap = len(ta & tb) / max(len(ta | tb), 1)
        base = min(base + overlap * 0.15, 1.0)

    # A volume/part/disc number present on only one side, or differing
    # between the two, strongly suggests different entries in a series
    # (e.g. 'Greatest Hits' vs 'Greatest Hits Vol. 2', or 'Vol. 1' vs 'Vol. 2').
    vol_a, vol_b = _volume_number(al), _volume_number(bl)
    if vol_a != vol_b:
        base = min(base, 0.35)

    return base


def _artist_similarity(a: str, b: str) -> float:
    """Token-set similarity for artist name strings."""
    a_unknown = not a or a.strip().lower() in _UNKNOWN_ARTIST_NAMES
    b_unknown = not b or b.strip().lower() in _UNKNOWN_ARTIST_NAMES
    if a_unknown or b_unknown:
        # Missing/placeholder artist tag — don't penalise, let name/year
        # drive the decision so real duplicates aren't hidden just because
        # one side was never tagged with an artist.
        return 0.5
    ta, tb = _tokenset(a), _tokenset(b)
    if not ta or not tb:
        return 0.5
    if ta == tb:
        return 1.0
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    # containment (e.g. 'Miles Davis' vs 'Miles Davis Quartet')
    if ta.issubset(tb) or tb.issubset(ta):
        overlap = min(overlap + 0.2, 1.0)
    return overlap


def _year_score(y1, y2) -> float:
    """
    Returns 1.0 for identical years, degrades gently for small gaps
    (remasters), returns 0.5 for missing years so they don't dominate.
    """
    if y1 is None or y2 is None:
        return 0.5
    diff = abs(int(y1) - int(y2))
    if diff == 0:
        return 1.0
    if diff <= 2:
        return 0.85
    if diff <= 10:
        return 0.6
    return 0.3


def score_pair(album_a, album_b) -> float:
    """Overall duplicate likelihood score in [0, 1]."""
    # Weights: album name 60%, artist 30%, year 10% -- artist/year are
    # intentionally soft so name similarity drives most decisions.
    name_s = _name_similarity(getattr(album_a, "album_name", "") or "", getattr(album_b, "album_name", "") or "")
    artist_s = _artist_similarity(getattr(album_a, "album_artist_names", "") or "", getattr(album_b, "album_artist_names", "") or "")
    year_s = _year_score(getattr(album_a, "release_year", None), getattr(album_b, "release_year", None))
    return name_s * 0.60 + artist_s * 0.30 + year_s * 0.10


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AlbumSnapshot:
    """Plain scalar copy of the Album fields score_pair/_bucket_key need.

    _DuplicateScanner reads these from its own background thread; handing it
    live ORM objects instead would mean two threads sharing one SQLAlchemy
    Session (the main thread's) the moment it touched an attribute.
    """

    album_id: int
    album_name: str
    album_artist_names: str
    release_year: int | None


class _DuplicateScanner(CancellableWorker):
    """Finds candidate duplicate album pairs above a threshold, comparing _AlbumSnapshots."""

    # Bucketing strategy: group albums by the first significant token of
    # album_name (lowercased, punctuation and edition tags stripped). Only
    # albums in the same bucket are compared, keeping complexity well below
    # O(n^2) in practice. Albums whose first token is a short stop-word
    # ('a', 'an', 'the') fall back to their second token. Albums with no
    # usable token go into a single catch-all bucket that is compared
    # exhaustively (usually tiny).

    progress = Signal(int)  # 0-100
    finished = Signal(list)  # list of (_AlbumSnapshot, _AlbumSnapshot, score)

    _STOPWORDS: ClassVar[set[str]] = {"a", "an", "the"}

    def __init__(self, albums: list[_AlbumSnapshot], threshold: float, parent=None):
        super().__init__(parent)
        self.albums = albums
        self.threshold = threshold

    # -- public slot so the caller can cancel mid-run
    def stop(self):
        self.request_cancel()

    def run(self):
        try:
            results = self._scan()
        except Exception:
            # Broad boundary catch: this runs on a background thread, so an
            # unexpected error (e.g. a non-numeric release_year blowing up
            # _year_score's int() call) would otherwise kill the thread
            # silently, leaving the dialog stuck on "Scanning..." forever.
            logger.exception("Duplicate album scan failed")
            self.finished.emit([])
            return

        if self.is_cancelled:
            return
        results.sort(key=lambda x: x[2], reverse=True)
        self.finished.emit(results)

    def _scan(self):
        albums = self.albums
        threshold = self.threshold
        results = []

        # Build buckets
        buckets: dict[str, list] = {}
        for album in albums:
            key = self._bucket_key(getattr(album, "album_name", "") or "")
            buckets.setdefault(key, []).append(album)

        bucket_list = list(buckets.values())
        total = len(bucket_list)

        for idx, bucket in enumerate(bucket_list):
            if self.is_cancelled:
                break
            n = len(bucket)
            for i in range(n):
                for j in range(i + 1, n):
                    s = score_pair(bucket[i], bucket[j])
                    if s >= threshold:
                        results.append((bucket[i], bucket[j], s))
            self.progress.emit(int((idx + 1) / max(total, 1) * 100))

        return results

    def _bucket_key(self, name: str) -> str:
        # Strip edition tags first so e.g. "Abbey Road" and "Abbey Road
        # (Deluxe Edition)" land in the same bucket and actually get
        # compared -- _name_similarity is specifically built to score that
        # pair as a near-certain match, but only if they reach the same
        # bucket in the first place.
        core = _strip_edition_tags(name)
        # Tokenize in original word order (not _tokenset's unordered set) so
        # "prefer the first token" means the literal first word, not
        # whichever token happens to sort first.
        tokens = re.sub(r"[^\w\s]", " ", core.lower()).split()
        for tok in tokens:
            if tok not in self._STOPWORDS and len(tok) > 1:
                return tok[:4]  # first 4 chars keeps buckets coarse
        return tokens[0][:4] if tokens else "__none__"


# ---------------------------------------------------------------------------
# AlbumMergeList dialog
# ---------------------------------------------------------------------------


class AlbumMergeList(QDialog):
    """
    Scans all albums for likely duplicates and lets the user select pairs
    to merge sequentially.

    Layout per row:
      [ ✓ ]  Album A name / artist / year  |  score %  |  Album B name / artist / year  |  Dismiss
    """

    _COL_CHECK = 0
    _COL_LEFT = 1
    _COL_SCORE = 2
    _COL_RIGHT = 3
    _COL_DISMISS = 4

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._pairs: list[tuple] = []  # (album_a, album_b, score)
        self._scanner: _DuplicateScanner | None = None
        self._albums_by_id: dict[int, object] = {}

        self.setWindowTitle("Find Duplicate Albums")
        self.resize(1000, 620)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Threshold slider row ---
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("Match threshold:"))

        self._threshold_label = QLabel("70 %")
        self._threshold_label.setMinimumWidth(40)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(30, 95)
        self._slider.setValue(70)
        self._slider.setTickInterval(5)
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.valueChanged.connect(self._on_slider_changed)

        self._scan_btn = QPushButton("Scan for Duplicates")
        self._scan_btn.clicked.connect(self._start_scan)

        slider_row.addWidget(self._slider)
        slider_row.addWidget(self._threshold_label)
        slider_row.addStretch()
        slider_row.addWidget(self._scan_btn)
        layout.addLayout(slider_row)

        # --- Progress bar (hidden until scan runs) ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.hide()
        layout.addWidget(self._progress)

        # --- Results table ---
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Merge?", "Album A", "Match", "Album B", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.itemClicked.connect(self._on_row_clicked)
        layout.addWidget(self._table)

        # --- Footer ---
        footer = QHBoxLayout()
        self._status_label = QLabel("Set a threshold and click Scan.")
        footer.addWidget(self._status_label)
        footer.addStretch()

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._select_all)
        self._select_all_btn.setEnabled(False)
        footer.addWidget(self._select_all_btn)

        self._next_btn = QPushButton("Merge Selected →")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._start_merge_queue)
        footer.addWidget(self._next_btn)

        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        layout.addLayout(footer)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _on_slider_changed(self, value):
        self._threshold_label.setText(f"{value} %")

    def _start_scan(self):
        if self._scanner is not None:
            # Disconnect the outgoing scanner's signals before replacing it,
            # so a cancel-then-rescan can't have the old scanner's deferred
            # `finished(partial_results)` land after the new scan starts and
            # overwrite its results.
            try:
                self._scanner.progress.disconnect(self._progress.setValue)
                self._scanner.finished.disconnect(self._on_scan_finished)
            except (RuntimeError, TypeError):
                pass
            if self._scanner.isRunning():
                self._scanner.stop()
                self._scanner.wait()

        threshold = self._slider.value() / 100.0

        try:
            albums = self.controller.get.get_all_entities("Album")
        except SQLAlchemyError as e:
            logger.error(f"Error loading albums: {e}")
            QMessageBox.critical(self, "Error", f"Could not load albums:\n{e}")
            return

        self._albums_by_id = {a.album_id: a for a in albums}
        snapshots = [
            _AlbumSnapshot(album_id=a.album_id, album_name=a.album_name or "", album_artist_names=getattr(a, "album_artist_names", "") or "", release_year=getattr(a, "release_year", None))
            for a in albums
        ]

        self._table.setRowCount(0)
        self._pairs.clear()
        self._next_btn.setEnabled(False)
        self._select_all_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText("Scanning…")

        self._scanner = _DuplicateScanner(snapshots, threshold, parent=self)
        self._scanner.progress.connect(self._progress.setValue)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.start()

    def _on_scan_finished(self, snapshot_pairs):
        self._progress.hide()
        self._scan_btn.setEnabled(True)

        # Map snapshots back to the real (main-thread) Album ORM objects the
        # rest of this dialog and the merge flow operate on.
        pairs = [
            (self._albums_by_id[sa.album_id], self._albums_by_id[sb.album_id], score) for sa, sb, score in snapshot_pairs if sa.album_id in self._albums_by_id and sb.album_id in self._albums_by_id
        ]

        dismissed = load_dismissed_pairs(DEFAULT_DISMISSED_DUPLICATES_PATH, "Album")
        pairs = [p for p in pairs if normalize_pair(p[0].album_id, p[1].album_id) not in dismissed]
        self._pairs = pairs

        if not pairs:
            self._status_label.setText("No duplicates found above threshold.")
            return

        self._status_label.setText(f"{len(pairs)} candidate pair(s) found. Check pairs to merge.")
        self._populate_table(pairs)
        self._select_all_btn.setEnabled(True)

    def _populate_table(self, pairs):
        self._table.setRowCount(0)
        for album_a, album_b, score in pairs:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # Checkbox column
            chk = QCheckBox()
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk.checkStateChanged.connect(self._update_next_btn)
            self._table.setCellWidget(row, self._COL_CHECK, chk_widget)

            # Left album
            left_text = self._album_label(album_a)
            left_item = QTableWidgetItem(left_text)
            left_item.setData(Qt.UserRole, album_a)
            self._table.setItem(row, self._COL_LEFT, left_item)

            # Score
            score_item = QTableWidgetItem(f"{score:.0%}")
            score_item.setTextAlignment(Qt.AlignCenter)
            if score >= 0.85:
                score_item.setForeground(Qt.darkGreen)
            elif score >= 0.70:
                score_item.setForeground(Qt.darkBlue)
            self._table.setItem(row, self._COL_SCORE, score_item)

            # Right album
            right_text = self._album_label(album_b)
            right_item = QTableWidgetItem(right_text)
            right_item.setData(Qt.UserRole, album_b)
            self._table.setItem(row, self._COL_RIGHT, right_item)

            # Dismiss column
            btn_dismiss = QPushButton("✖ Dismiss")
            btn_dismiss.setToolTip("Not a duplicate -- don't suggest this pair again")
            btn_dismiss.clicked.connect(lambda _checked=False, b=btn_dismiss: self._dismiss_row(b))
            self._table.setCellWidget(row, self._COL_DISMISS, btn_dismiss)

    def _find_row_for_widget(self, widget: QWidget, column: int) -> int | None:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, column) is widget:
                return row
        return None

    def _dismiss_row(self, btn_dismiss: QPushButton) -> None:
        """Record the row's album pair as permanently not-a-duplicate and
        hide the row. Hidden rather than removed: QTableWidget.removeRow()
        destroys cell widgets immediately, and this handler is still
        running inside btn_dismiss's own clicked() call stack -- destroying
        it out from under itself here would be a use-after-free. Hiding
        needs no such care, and every other row-scanning method
        (_select_all, _update_next_btn, _checked_pairs) already skips
        hidden rows so a dismissed pair can't come back via Select All."""
        row = self._find_row_for_widget(btn_dismiss, self._COL_DISMISS)
        if row is None:
            return

        album_a = self._table.item(row, self._COL_LEFT).data(Qt.UserRole)
        album_b = self._table.item(row, self._COL_RIGHT).data(Qt.UserRole)
        dismiss_pair(DEFAULT_DISMISSED_DUPLICATES_PATH, "Album", album_a.album_id, album_b.album_id)
        self._pairs = [p for p in self._pairs if not (p[0] is album_a and p[1] is album_b)]

        chk = self._get_checkbox(row)
        if chk:
            chk.setChecked(False)
        self._table.setRowHidden(row, True)
        self._update_next_btn()

    def _album_label(self, album) -> str:
        name = getattr(album, "album_name", "") or "Unknown"
        artist = getattr(album, "album_artist_names", "") or ""
        year = getattr(album, "release_year", None)
        year_s = str(year) if year else "?"
        return f"{name}  ({artist}, {year_s})"

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_row_clicked(self, item):
        """Clicking anywhere on a row toggles the checkbox."""
        row = item.row()
        chk = self._get_checkbox(row)
        if chk:
            chk.setChecked(not chk.isChecked())

    def _get_checkbox(self, row) -> QCheckBox | None:
        widget = self._table.cellWidget(row, self._COL_CHECK)
        if widget:
            return widget.findChild(QCheckBox)
        return None

    def _select_all(self):
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            chk = self._get_checkbox(row)
            if chk:
                chk.setChecked(True)

    def _update_next_btn(self):
        any_checked = any((chk := self._get_checkbox(r)) and chk.isChecked() for r in range(self._table.rowCount()) if not self._table.isRowHidden(r))
        self._next_btn.setEnabled(any_checked)

    def _checked_pairs(self) -> list[tuple]:
        result = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            chk = self._get_checkbox(row)
            if chk and chk.isChecked():
                album_a = self._table.item(row, self._COL_LEFT).data(Qt.UserRole)
                album_b = self._table.item(row, self._COL_RIGHT).data(Qt.UserRole)
                result.append((album_a, album_b))
        return result

    # ------------------------------------------------------------------
    # Merge queue
    # ------------------------------------------------------------------

    def _start_merge_queue(self):
        pairs = self._checked_pairs()
        if not pairs:
            return
        self._run_queue(pairs)

    def _run_queue(self, pairs: list[tuple]):
        """Open AlbumMergeDialog for each pair in sequence, then close."""
        total = len(pairs)
        for idx, (album_a, album_b) in enumerate(pairs, start=1):
            # An earlier merge in this queue may have already deleted one of
            # these albums (e.g. the same album is a high-scoring duplicate
            # of two different albums) -- re-check both still exist before
            # opening the dialog on a stale/deleted ORM object.
            try:
                still_a = self.controller.get.get_entity_object("Album", album_id=album_a.album_id)
                still_b = self.controller.get.get_entity_object("Album", album_id=album_b.album_id)
            except SQLAlchemyError as e:
                logger.error(f"Error re-checking merge pair before dialog: {e}")
                continue
            if not still_a or not still_b:
                self._status_label.setText(f"Skipped a pair -- one album was already merged/deleted ({idx} of {total}).")
                continue

            dlg = AlbumMergeDialog(self.controller, preload_source=still_a, preload_target=still_b, parent=self)
            dlg.setWindowTitle(f"Merge Duplicate Albums ({idx} of {total})")
            # accept() or reject() both just advance to next pair
            dlg.exec()

        self.accept()


# ---------------------------------------------------------------------------
# AlbumMergeDialog
# ---------------------------------------------------------------------------


class AlbumMergeDialog(MergeDBDialog):
    """
    Album-specific merge dialog.

    Overrides:
      - _get_related_count  : returns total track count across all discs
      - _build_entity_info  : adds artist, year, and track count to the panel
    """

    def __init__(self, controller, parent=None, preload_source=None, preload_target=None):
        super().__init__(controller, "Album", parent=parent, preload_source=preload_source, preload_target=preload_target)

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def _get_related_count(self, entity_id: int) -> int:
        """Return total track count for this album (across all discs)."""
        try:
            album = self.controller.get.get_entity_object("Album", album_id=entity_id)
            total = 0
            for disc in getattr(album, "discs", []) or []:
                total += len(getattr(disc, "tracks", []) or [])
            return total
        except SQLAlchemyError as e:
            logger.error(f"Error getting track count for album {entity_id}: {e}")
            return 0

    def _build_entity_info(self, entity, side: str) -> str:
        if not entity:
            return "No album selected"

        name = getattr(entity, "album_name", "Unknown") or "Unknown"
        artist = getattr(entity, "album_artist_names", "") or ""
        year = getattr(entity, "release_year", None)
        year_s = str(year) if year else "Unknown"

        track_count = self._get_related_count(getattr(entity, "album_id", None))
        track_s = f"{track_count} track{'s' if track_count != 1 else ''}"

        info = f"<b>{name}</b><br>"
        if artist:
            info += f"{artist}<br>"
        info += f"{year_s} &nbsp;·&nbsp; {track_s}"
        return info


# ---------------------------------------------------------------------------
# AlbumMerge  —  primary engine / entry point
# ---------------------------------------------------------------------------


class AlbumMerge:
    """Entry point for album merge operations: merge a known pair or open the duplicate finder."""

    def __init__(self, controller):
        self.controller = controller

    def merge_known(self, album, parent=None) -> bool:
        """
        Open AlbumMergeDialog with *album* pre-loaded as the source.
        The user searches/selects the target inside the dialog.

        Returns True if the merge was completed, False if cancelled.
        """
        dlg = AlbumMergeDialog(self.controller, preload_source=album, parent=parent)
        return dlg.exec() == QDialog.Accepted

    def open_list(self, parent=None):
        """Open the duplicate-finder list dialog."""
        dlg = AlbumMergeList(self.controller, parent=parent)
        dlg.exec()
