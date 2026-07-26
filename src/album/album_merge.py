import re
from difflib import SequenceMatcher

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

from src.common.base_merge_dialog import MergeDBDialog
from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger

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
_VOLUME_RE = re.compile(
    r"\b(?:vol(?:ume)?s?|pt|part|disc|disk|book)\.?\s*(\d+)\b",
    re.IGNORECASE,
)

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
    """
    Substring-aware similarity between two strings.

    Uses SequenceMatcher as the base score but boosts when one name is
    contained within the other (handles 'Swings with Billy Mays' vs
    'Frank Sinatra Swings with Billy Mays'). Edition/soundtrack tags
    ('(Remastered)', '(Original Motion Picture Soundtrack)', ...) are
    stripped before comparing so shared boilerplate doesn't inflate the
    score of otherwise-unrelated albums, and titles that are identical
    once such a tag is removed are treated as a near-certain match.
    Differing volume/part/disc numbers cap the score, since those mark
    distinct entries in a series rather than duplicates.
    """
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
    """
    Overall duplicate likelihood score in [0, 1].

    Weights: album name 60 %, artist 30 %, year 10 %.
    Artist/year weights are intentionally soft so name similarity
    drives most decisions.
    """
    name_s = _name_similarity(
        getattr(album_a, "album_name", "") or "",
        getattr(album_b, "album_name", "") or "",
    )
    artist_s = _artist_similarity(
        getattr(album_a, "album_artist_names", "") or "",
        getattr(album_b, "album_artist_names", "") or "",
    )
    year_s = _year_score(
        getattr(album_a, "release_year", None),
        getattr(album_b, "release_year", None),
    )
    return name_s * 0.60 + artist_s * 0.30 + year_s * 0.10


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class _DuplicateScanner(CancellableWorker):
    """
    Finds candidate duplicate pairs above a given threshold.

    Bucketing strategy: group albums by the first significant token of
    album_name (lowercased, punctuation stripped).  Only albums in the same
    bucket are compared, keeping complexity well below O(n²) in practice.
    Albums whose first token is a short stop-word ('a', 'an', 'the') fall
    back to their second token.  Albums with no usable token go into a single
    catch-all bucket that is compared exhaustively (usually tiny).
    """

    progress = Signal(int)  # 0-100
    finished = Signal(list)  # list of (album_a, album_b, score)

    _STOPWORDS = {"a", "an", "the"}

    def __init__(self, albums, threshold: float, parent=None):
        super().__init__(parent)
        self.albums = albums
        self.threshold = threshold

    # -- public slot so the caller can cancel mid-run
    def stop(self):
        self.request_cancel()

    def run(self):
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

        results.sort(key=lambda x: x[2], reverse=True)
        self.finished.emit(results)

    def _bucket_key(self, name: str) -> str:
        tokens = list(_tokenset(name))  # already lowercased
        # prefer the first non-stopword token
        for tok in sorted(tokens, key=len, reverse=True):
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
      [ ✓ ]  Album A name / artist / year  |  score %  |  Album B name / artist / year
    """

    _COL_CHECK = 0
    _COL_LEFT = 1
    _COL_SCORE = 2
    _COL_RIGHT = 3

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._pairs: list[tuple] = []  # (album_a, album_b, score)
        self._scanner: _DuplicateScanner | None = None

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
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Album A", "Match", "Album B"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
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
        if self._scanner and self._scanner.isRunning():
            self._scanner.stop()
            self._scanner.wait()

        threshold = self._slider.value() / 100.0

        try:
            albums = self.controller.get.get_all_entities("Album")
        except Exception as e:
            logger.error(f"Error loading albums: {e}")
            QMessageBox.critical(self, "Error", f"Could not load albums:\n{e}")
            return

        self._table.setRowCount(0)
        self._pairs.clear()
        self._next_btn.setEnabled(False)
        self._select_all_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText("Scanning…")

        self._scanner = _DuplicateScanner(albums, threshold, parent=self)
        self._scanner.progress.connect(self._progress.setValue)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.start()

    def _on_scan_finished(self, pairs):
        self._progress.hide()
        self._scan_btn.setEnabled(True)
        self._pairs = pairs

        if not pairs:
            self._status_label.setText("No duplicates found above threshold.")
            return

        self._status_label.setText(
            f"{len(pairs)} candidate pair(s) found. Check pairs to merge."
        )
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
            chk.stateChanged.connect(self._update_next_btn)
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
            chk = self._get_checkbox(row)
            if chk:
                chk.setChecked(True)

    def _update_next_btn(self):
        any_checked = any(
            (chk := self._get_checkbox(r)) and chk.isChecked()
            for r in range(self._table.rowCount())
        )
        self._next_btn.setEnabled(any_checked)

    def _checked_pairs(self) -> list[tuple]:
        result = []
        for row in range(self._table.rowCount()):
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
        for album_a, album_b in pairs:
            dlg = AlbumMergeDialog(
                self.controller,
                preload_source=album_a,
                preload_target=album_b,
                parent=self,
            )
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

    def __init__(
        self, controller, parent=None, preload_source=None, preload_target=None
    ):
        super().__init__(
            controller,
            "Album",
            parent=parent,
            preload_source=preload_source,
            preload_target=preload_target,
        )

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
        except Exception as e:
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
    """
    Entry point for all album merge operations.

    Usage
    -----
    engine = AlbumMerge(controller)

    # Open dialog with one known album pre-loaded as source:
    engine.merge_known(album, parent=some_widget)

    # Open the duplicate-finder list:
    engine.open_list(parent=some_widget)

    # Programmatic merge (no UI):
    engine.execute_merge(source_id, target_id)
    """

    def __init__(self, controller):
        self.controller = controller

    def merge_known(self, album, parent=None) -> bool:
        """
        Open AlbumMergeDialog with *album* pre-loaded as the source.
        The user searches/selects the target inside the dialog.

        Returns True if the merge was completed, False if cancelled.
        """
        dlg = AlbumMergeDialog(
            self.controller,
            preload_source=album,
            parent=parent,
        )
        return dlg.exec() == QDialog.Accepted

    def open_list(self, parent=None):
        """Open the duplicate-finder list dialog."""
        dlg = AlbumMergeList(self.controller, parent=parent)
        dlg.exec()

    def execute_merge(self, source_id: int, target_id: int) -> bool:
        """
        Perform a merge programmatically with no UI.

        The source album is merged into the target and then deleted.
        Returns True on success.
        """
        try:
            success = self.controller.merge.merge_entities(
                "Album", source_id, target_id
            )
            if not success:
                logger.error(
                    f"merge_entities returned falsy for "
                    f"source={source_id} target={target_id}"
                )
            return bool(success)
        except Exception as e:
            logger.error(f"execute_merge failed: {e}")
            return False
