"""
place_fuzzy_match.py

Background scan for likely-duplicate places, and the review dialog used to
merge whatever it finds. Mirrors src/artist/artist_fuzzy_match.py, but a
place's leaf name alone is a weak duplicate signal -- lots of real, distinct
places share a name (Paris, Springfield, San Jose, ...) across different
parents. So a pair must also have a similar ancestor chain (or no ancestor
chain on either side to compare) before it's flagged, using the same
"City, State, Country" chain-building logic the completer-popup hint already
uses in entity_completer_context.py.
"""

from collections import defaultdict
from difflib import SequenceMatcher
import re
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.cancellable_worker import CancellableWorker
from src.common.dialogs.fuzzy_match_dialog import BaseFuzzyMatchDialog
from src.common.widgets.entity_completer_context import _ancestor_chain, _place_context
from src.common.widgets.qt_text import esc_amp as _esc_amp
from src.foundation.logger_config import logger

_PUNCT_RE = re.compile(r"[^\w\s]")

NAME_THRESHOLD = 0.85  # required place_name similarity
CHAIN_THRESHOLD = 0.6  # required ancestor-chain similarity, when both sides have one


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = _PUNCT_RE.sub("", text)
    return " ".join(text.split())


def _blocking_keys(name: str) -> set:
    """Keys used to bucket a place name for pairwise comparison (same
    prefix + last-token strategy as the artist scan)."""
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
    """Similarity ratio between two places' ancestor chains, in [0, 1].

    A chain-less side (no parent, or a parent with no further context --
    e.g. a top-level country row) carries no information to contradict a
    name match, so it's treated as a pass (1.0) rather than a mismatch.
    Only when *both* sides have a chain to compare does a mismatch (e.g.
    "Paris" under France vs. "Paris" under the United States) gate the pair
    out.
    """
    chain_a = _ancestor_chain(place_a)
    chain_b = _ancestor_chain(place_b)
    if not chain_a or not chain_b:
        return 1.0
    text_a = _normalise(", ".join(chain_a))
    text_b = _normalise(", ".join(chain_b))
    return SequenceMatcher(None, text_a, text_b).ratio()


# ---------------------------------------------------------------------------
# PlaceFuzzyMatchWorker
# ---------------------------------------------------------------------------


class PlaceFuzzyMatchWorker(CancellableWorker):
    """
    Background worker that finds fuzzy-duplicate place pairs.

    Blocks places by normalised-name prefix plus last-name-token, same as
    the artist scan, to avoid comparing every place against every other
    place. A pair must clear both the name-similarity threshold and the
    ancestor-chain-similarity threshold (see place_chain_similarity) to be
    flagged, and is skipped outright if both sides carry a different,
    non-empty MBID -- MusicBrainz has already confirmed those are distinct
    places.

    Signals:
        progress(current, total)
        finished(matches)  - list[(place_a, place_b, score_pct)]
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, places: list, name_threshold: float, chain_threshold: float, parent=None):
        super().__init__(parent)
        self._places = places
        self._name_threshold = name_threshold
        self._chain_threshold = chain_threshold

    def run(self):
        try:
            matches = self._find_matches()
            self.finished.emit(matches)
        except Exception as e:
            # Intentional broad boundary catch: this is a QThread's run() body
            # with no caller frame to catch anything that escapes it -- any
            # exception must be turned into the error signal instead of
            # silently killing the thread and leaving the progress dialog
            # spinning forever.
            logger.error(f"PlaceFuzzyMatchWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    def _find_matches(self) -> list:
        blocks = defaultdict(list)
        for place in self._places:
            for key in _blocking_keys(place.place_name):
                blocks[key].append(place)
        blocks = {k: v for k, v in blocks.items() if len(v) >= 2}

        total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in blocks.values())
        self.progress.emit(0, max(total_pairs, 1))

        matches = []
        # A place can appear in more than one block (prefix key + last-token
        # key), so the same pair can surface twice -- dedupe here rather than
        # skip the second occurrence up front, so `checked` still tracks
        # every loop iteration for accurate progress.
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
                        if a.MBID and b.MBID and a.MBID != b.MBID:
                            continue
                        name_ratio = place_name_similarity(a.place_name, b.place_name)
                        if name_ratio >= self._name_threshold:
                            chain_ratio = place_chain_similarity(a, b)
                            if chain_ratio >= self._chain_threshold:
                                matches.append((a, b, round(name_ratio * 100)))
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
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel(
            "✔ Check pairs to merge | 🅐🅑 Select which place to keep | ✖ Leave unchecked to ignore"
        )
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

            # Radio buttons for place selection. The ancestor-chain hint
            # (same text the completer popup shows) tells apart two
            # same-named places in different countries/regions; the
            # recursive association count is the strongest signal for which
            # entry is the canonical one.
            radio_a = QRadioButton(_esc_amp(self._radio_label(place_a)))
            radio_a.entity = place_a
            radio_b = QRadioButton(_esc_amp(self._radio_label(place_b)))
            radio_b.entity = place_b
            radio_a.setChecked(True)  # Default to first place

            hbox.addWidget(radio_a)
            hbox.addWidget(radio_b)
            hbox.addWidget(QLabel(f"Similarity: {score}%"))
            hbox.addStretch()

            self.match_widgets.append((chk_merge, radio_a, radio_b))
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
        hint = _place_context(place)
        assoc_count = getattr(place, "recursive_association_count", 0)
        label = place.place_name
        if hint:
            label += f" ({hint})"
        return f"{label} — {assoc_count} association{'s' if assoc_count != 1 else ''}"

    def _notify_no_jobs(self) -> None:
        QMessageBox.warning(
            self, "No Merges", "No pairs were merged (none checked or errors occurred)"
        )
