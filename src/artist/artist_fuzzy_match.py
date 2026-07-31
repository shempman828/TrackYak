import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
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
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = _PUNCT_RE.sub("", text)
    return " ".join(text.split())


def _blocking_keys(name: str) -> set:
    """Keys used to bucket an artist name for pairwise comparison.

    The normalised-prefix key catches near-identical spellings. The
    last-token key additionally catches pairs that only differ earlier in
    the name -- e.g. an initialised first name ("J. Lennon" vs "John
    Lennon") or a dropped "The" prefix -- which wouldn't share a 3-char
    prefix and would otherwise never be compared at all.
    """
    norm = _normalise(name)
    tokens = norm.split()
    keys = set()
    if norm[:3]:
        keys.add(norm[:3])
    if tokens:
        keys.add(tokens[-1])
    return keys


_INITIALS_MATCH_FLOOR = 0.92


def _tokens_match_with_initials(tokens_a: list, tokens_b: list) -> bool:
    """True if every token pair is identical, or one side is a single
    initial that the other side starts with (e.g. "j" vs "john")."""
    if not tokens_a or len(tokens_a) != len(tokens_b):
        return False
    for a, b in zip(tokens_a, tokens_b):
        if a == b:
            continue
        if len(a) == 1 and b.startswith(a):
            continue
        if len(b) == 1 and a.startswith(b):
            continue
        return False
    return True


def artist_name_similarity(name_a: str, name_b: str) -> float:
    """Similarity ratio between two artist names, in [0, 1].

    Plain character-level SequenceMatcher.ratio() scores initialised names
    like "J. Lennon" too low against "John Lennon" -- the shared surname
    gets diluted by the raw character-count difference. When every token
    lines up exactly or as an initial, floor the score at
    _INITIALS_MATCH_FLOOR so these pairs clear the usual fuzzy-match
    thresholds.
    """
    norm_a, norm_b = _normalise(name_a), _normalise(name_b)
    ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    if _tokens_match_with_initials(norm_a.split(), norm_b.split()):
        ratio = max(ratio, _INITIALS_MATCH_FLOOR)
    return ratio


# ---------------------------------------------------------------------------
# ArtistFuzzyMatchWorker
# ---------------------------------------------------------------------------


class ArtistFuzzyMatchWorker(CancellableWorker):
    """
    Background worker that finds fuzzy-duplicate artist name pairs.

    Blocks artists by normalised-name prefix (same strategy as
    duplicate_finder.py) plus last name-token, to avoid comparing every
    artist against every other artist while still catching pairs that only
    share a surname (see `_blocking_keys`). Unlike the original ad-hoc
    implementation this was extracted from, cancellation is checked and
    progress is emitted every 500 pairs
    *inside* the block loop rather than only between blocks — a single
    oversized block (e.g. hundreds of artists sharing a common name prefix)
    could otherwise run for a long time with no feedback and an unresponsive
    Cancel button.

    Signals:
        progress(current, total)
        finished(matches)  - list[(artist_a, artist_b, score_pct)]
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, artists: list, threshold: float, parent=None):
        super().__init__(parent)
        self._artists = artists
        self._threshold = threshold

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
            logger.error(f"ArtistFuzzyMatchWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    def _find_matches(self) -> list:
        blocks = defaultdict(list)
        for artist in self._artists:
            for key in _blocking_keys(artist.artist_name):
                blocks[key].append(artist)
        blocks = {k: v for k, v in blocks.items() if len(v) >= 2}

        total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in blocks.values())
        self.progress.emit(0, max(total_pairs, 1))

        matches = []
        # An artist can appear in more than one block (prefix key + last-
        # token key), so the same pair can surface twice -- dedupe here
        # rather than skip the second occurrence up front, so `checked`
        # still tracks every loop iteration for accurate progress.
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
                    pair_key = tuple(sorted((a.artist_id, b.artist_id)))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        ratio = artist_name_similarity(a.artist_name, b.artist_name)
                        if ratio >= self._threshold:
                            matches.append((a, b, round(ratio * 100)))
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
            logger.info(f"Artist fuzzy match scan stopped by user after {checked:,} pairs")
        return matches


# ---------------------------------------------------------------------------
# Background merge worker
# ---------------------------------------------------------------------------


class _ArtistMergeWorker(CancellableWorker):
    """Runs the checked merges off the UI thread so a large batch doesn't
    freeze the dialog (or starve other threads, e.g. audio playback) and
    reports progress as each pair completes."""

    progress = Signal(int, int)  # current, total
    finished = Signal(int, int)  # success_count, total

    def __init__(self, controller, jobs: list, parent=None):
        super().__init__(parent)
        self.controller = controller
        # jobs: list of (old_artist, new_artist)
        self.jobs = jobs

    def run(self) -> None:
        total = len(self.jobs)
        success_count = 0

        for idx, (old_artist, new_artist) in enumerate(self.jobs):
            try:
                logger.info(
                    f"Merging {old_artist.artist_name} (ID: {old_artist.artist_id}) "
                    f"into {new_artist.artist_name} (ID: {new_artist.artist_id})"
                )
                merged = self.controller.merge.merge_entities(
                    "Artist",
                    old_artist.artist_id,
                    new_artist.artist_id,
                )
                if not merged:
                    logger.error(
                        f"Failed to merge {old_artist.artist_name} → "
                        f"{new_artist.artist_name}: merge_entities returned False"
                    )
                    self.progress.emit(idx + 1, total)
                    continue

                logger.info(
                    f"adding alias for {old_artist.artist_name} to {new_artist.artist_name}"
                )
                self.controller.add.add_entity(
                    "ArtistAlias",
                    artist_id=new_artist.artist_id,
                    alias_name=old_artist.artist_name,
                )
                success_count += 1
            except SQLAlchemyError as e:
                logger.error(
                    f"Failed to merge {old_artist.artist_name} → {new_artist.artist_name}: {e}"
                )

            self.progress.emit(idx + 1, total)

        self.finished.emit(success_count, total)


# Fuzzy Match Dialog
# -------------------------
class FuzzyMatchDialog(QDialog):
    """Dialog to display fuzzy matches and allow merging."""

    def __init__(self, matches: List[tuple], controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.matches = sorted(
            matches, key=lambda x: x[2], reverse=True
        )  # x[2] is the score
        self.setWindowTitle("Merge Artists")
        self.setMinimumSize(600, 400)
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel(
            "✔ Check pairs to merge | 🅐🅑 Select which artist to keep | ✖ Leave unchecked to ignore"
        )
        layout.addWidget(lbl_instructions)

        # Scrollable match list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.match_layout = QVBoxLayout(content)
        self.match_layout.setSpacing(10)

        # Add each match pair with controls
        self.match_widgets = []
        for artist_a, artist_b, score in self.matches:
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            hbox = QHBoxLayout(frame)

            # Checkbox to enable/disable merging for this pair
            chk_merge = QCheckBox()
            chk_merge.setChecked(False)  # Default to unchecked
            hbox.addWidget(chk_merge)

            # Radio buttons for artist selection. Role count (album + track
            # credits) is shown alongside each name since it's the strongest
            # signal for which spelling is the canonical one -- e.g. "Lew"
            # with 63 credits vs. "Lewis" with 2 suggests "Lewis" is the typo.
            radio_a = QRadioButton(
                f"{artist_a.artist_name} ({artist_a.role_count} roles)"
            )
            radio_a.artist = artist_a
            radio_b = QRadioButton(
                f"{artist_b.artist_name} ({artist_b.role_count} roles)"
            )
            radio_b.artist = artist_b
            radio_a.setChecked(True)  # Default to first artist

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

        self._worker: _ArtistMergeWorker | None = None

    def _perform_merge(self) -> None:
        """Kick off a background merge of the checked pairs with the
        user-selected canonical artist, showing progress as it runs."""
        jobs = []
        for chk_merge, radio_a, radio_b in self.match_widgets:
            if not chk_merge.isChecked():
                continue  # Skip unchecked pairs

            # Determine which artist to keep
            if radio_a.isChecked():
                old_artist = radio_b.artist
                new_artist = radio_a.artist
            else:
                old_artist = radio_a.artist
                new_artist = radio_b.artist

            jobs.append((old_artist, new_artist))

        if not jobs:
            QMessageBox.warning(
                self,
                "No Merges",
                "No pairs were merged (none checked or errors occurred)",
            )
            return

        self.btn_merge.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._progress.setRange(0, len(jobs))
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText(f"Merging 0/{len(jobs)}…")
        self._status_label.show()

        self._worker = _ArtistMergeWorker(self.controller, jobs, parent=self)
        self._worker.progress.connect(self._on_merge_progress)
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.start()

    def _on_merge_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)
        self._status_label.setText(f"Merging {current}/{total}…")

    def _on_merge_finished(self, success_count: int, total: int) -> None:
        self._progress.hide()
        self._status_label.hide()
        self.btn_merge.setEnabled(True)
        self.btn_cancel.setEnabled(True)

        if success_count > 0:
            QMessageBox.information(
                self,
                "Merge Complete",
                f"Successfully merged {success_count}/{total} pairs",
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "No Merges",
                "No pairs were merged (none checked or errors occurred)",
            )

    def reject(self) -> None:
        # Cancel button is disabled while a merge is running, but guard
        # against Escape/close-button closing the dialog mid-merge anyway.
        if self._worker is not None and self._worker.isRunning():
            return
        super().reject()
