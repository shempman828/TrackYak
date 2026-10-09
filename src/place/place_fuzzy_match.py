"""Duplicate-place scan (name plus ancestor-chain similarity) and the dialog to merge what it finds."""

from collections import defaultdict, namedtuple
from difflib import SequenceMatcher
import re
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from src.common.cancellable_worker import CancellableWorker
from src.common.dialogs.fuzzy_match_dialog import BaseFuzzyMatchDialog
from src.common.widgets.entity_completer_context import _ancestor_chain, _place_context
from src.common.widgets.qt_text import esc_amp as _esc_amp
from src.foundation.logger_config import logger

_PUNCT_RE = re.compile(r"[^\w\s]")

_ScanRecord = namedtuple("_ScanRecord", "place place_id name mbid chain")

NAME_THRESHOLD = 0.85  # required place_name similarity
CHAIN_THRESHOLD = 0.6  # required ancestor-chain similarity, when both sides have one


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = _PUNCT_RE.sub("", text)
    return " ".join(text.split())


def _blocking_keys(name: str) -> set:
    """Buckets for a place name (3-letter prefix and last token), as in the artist scan."""
    norm = _normalise(name)
    tokens = norm.split()
    keys = set()
    if norm[:3]:
        keys.add(norm[:3])
    if tokens:
        keys.add(tokens[-1])
    return keys


def place_name_similarity(name_a: str, name_b: str) -> float:
    """Similarity ratio between two place names, in [0, 1]."""
    return SequenceMatcher(None, _normalise(name_a), _normalise(name_b)).ratio()


def place_chain_similarity(place_a, place_b) -> float:
    """Similarity ratio between two places' ancestor chains, in [0, 1]."""
    return chain_similarity(_ancestor_chain(place_a), _ancestor_chain(place_b))


def chain_similarity(chain_a, chain_b) -> float:
    """Similarity ratio between two ancestor-name chains, in [0, 1]."""
    # A side with no chain cannot contradict a name match, so it passes (1.0).
    if not chain_a or not chain_b:
        return 1.0
    text_a = _normalise(", ".join(chain_a))
    text_b = _normalise(", ".join(chain_b))
    return SequenceMatcher(None, text_a, text_b).ratio()


# ---------------------------------------------------------------------------
# PlaceFuzzyMatchWorker
# ---------------------------------------------------------------------------


class PlaceFuzzyMatchWorker(CancellableWorker):
    """Find likely-duplicate place pairs on a worker thread; emits finished([(place_a, place_b, score_pct)])."""

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, places: list, name_threshold: float, chain_threshold: float, parent=None):
        super().__init__(parent)
        # Read every ORM attribute here, on the GUI thread: run() must not touch
        # the GUI thread's session (lazy loads of `parent`, expired columns).
        self._records = [_ScanRecord(p, p.place_id, p.place_name or "", p.MBID, _ancestor_chain(p)) for p in places]
        self._name_threshold = name_threshold
        self._chain_threshold = chain_threshold

    def run(self):
        """Run the scan and emit finished, or error on any exception."""
        try:
            matches = self._find_matches()
            self.finished.emit(matches)
        except Exception as e:
            # Broad on purpose: nothing above run() can catch it, and the progress dialog must close.
            logger.error(f"PlaceFuzzyMatchWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    def _find_matches(self) -> list:
        """Compare the places in each block and return the pairs over both thresholds."""
        blocks = defaultdict(list)
        for record in self._records:
            for key in _blocking_keys(record.name):
                blocks[key].append(record)
        blocks = {k: v for k, v in blocks.items() if len(v) >= 2}

        total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in blocks.values())
        self.progress.emit(0, max(total_pairs, 1))

        matches = []
        # A pair can be in two blocks; dedupe here so `checked` still counts every iteration for progress.
        seen_pairs = set()
        checked = 0
        last_emitted = 0
        stopped = False

        for block in blocks.values():
            if self.is_cancelled:
                stopped = True
                break
            m = len(block)
            for i in range(m):
                if self.is_cancelled:
                    stopped = True
                    break
                for j in range(i + 1, m):
                    a, b = block[i], block[j]
                    checked += 1
                    pair_key = tuple(sorted((a.place_id, b.place_id)))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        # Two different MBIDs: MusicBrainz says these are distinct places.
                        if a.mbid and b.mbid and a.mbid != b.mbid:
                            continue
                        name_ratio = place_name_similarity(a.name, b.name)
                        if name_ratio >= self._name_threshold and chain_similarity(a.chain, b.chain) >= self._chain_threshold:
                            matches.append((a.place, b.place, round(name_ratio * 100)))
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

        self.progress.emit(checked if stopped else total_pairs, max(total_pairs, 1))
        if stopped:
            logger.info(f"Place fuzzy match scan stopped by user after {checked:,} pairs")
        return matches


# Fuzzy Match Dialog
# -------------------------
class FuzzyMatchDialog(BaseFuzzyMatchDialog):
    """Dialog to display fuzzy place matches and allow merging."""

    _ENTITY_TYPE = "Place"
    _ID_ATTR = "place_id"
    _NAME_ATTR = "place_name"

    def __init__(self, matches: list[tuple], controller: Any, parent=None):
        super().__init__(matches, controller, "Merge Places", parent)

    def init_ui(self) -> None:
        """Build the instructions, one row per pair, and the merge controls."""
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel("✔ Check pairs to merge | 🅐🅑 Select which place to keep | ✖ Leave unchecked to ignore | Dismiss to never suggest a pair again")
        layout.addWidget(lbl_instructions)

        # Scrollable match list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.match_layout = QVBoxLayout(content)
        self.match_layout.setSpacing(10)

        # Add each match pair with controls
        for place_a, place_b, score in self.matches:
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            hbox = QHBoxLayout(frame)

            # Checkbox to enable/disable merging for this pair
            chk_merge = QCheckBox()
            chk_merge.setChecked(False)  # Default to unchecked
            hbox.addWidget(chk_merge)

            # The chain hint tells same-named places apart; the association count suggests which to keep.
            radio_a = QRadioButton(_esc_amp(self._radio_label(place_a)))
            radio_a.entity = place_a
            radio_b = QRadioButton(_esc_amp(self._radio_label(place_b)))
            radio_b.entity = place_b
            radio_a.setChecked(True)  # Default to first place

            hbox.addWidget(radio_a)
            hbox.addWidget(radio_b)
            hbox.addWidget(QLabel(f"Similarity: {score}%"))
            hbox.addStretch()

            widgets_tuple = (chk_merge, radio_a, radio_b)
            btn_dismiss = QPushButton("✖ Dismiss")
            btn_dismiss.setToolTip("Not a duplicate -- don't suggest this pair again")
            btn_dismiss.clicked.connect(lambda _checked=False, a=place_a, b=place_b, f=frame, wt=widgets_tuple: self._dismiss_pair(a, b, f, wt))
            hbox.addWidget(btn_dismiss)

            self.match_widgets.append(widgets_tuple)
            self.match_layout.addWidget(frame)

        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Action buttons
        btn_box = QHBoxLayout()
        self.btn_merge = QPushButton("Merge Checked Pairs")
        self.btn_merge.clicked.connect(self._perform_merge)
        btn_box.addWidget(self.btn_merge)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        layout.addLayout(btn_box)

        # Progress bar (hidden until a merge is running)
        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.hide()
        layout.addWidget(self._status_label)

    @staticmethod
    def _radio_label(place) -> str:
        """Radio text: name, ancestor hint, and association count."""
        hint = _place_context(place)
        assoc_count = getattr(place, "recursive_association_count", 0)
        label = place.place_name
        if hint:
            label += f" ({hint})"
        return f"{label} — {assoc_count} association{'s' if assoc_count != 1 else ''}"

    def _notify_no_jobs(self) -> None:
        """Tell the user that no pair was merged."""
        QMessageBox.warning(self, "No Merges", "No pairs were merged (none checked or errors occurred)")
