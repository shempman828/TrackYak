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
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = _PUNCT_RE.sub("", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# ArtistFuzzyMatchWorker
# ---------------------------------------------------------------------------


class ArtistFuzzyMatchWorker(CancellableWorker):
    """
    Background worker that finds fuzzy-duplicate artist name pairs.

    Uses the same blocking strategy as duplicate_finder.py (first 3 chars of
    the normalised name) to avoid comparing every artist against every other
    artist. Unlike the original ad-hoc implementation this was extracted
    from, cancellation is checked and progress is emitted every 500 pairs
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
            logger.error(f"ArtistFuzzyMatchWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    def _find_matches(self) -> list:
        blocks = defaultdict(list)
        for artist in self._artists:
            key = _normalise(artist.artist_name)[:3]
            if key:
                blocks[key].append(artist)
        blocks = {k: v for k, v in blocks.items() if len(v) >= 2}

        total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in blocks.values())
        self.progress.emit(0, max(total_pairs, 1))

        matches = []
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
                    ratio = SequenceMatcher(
                        None, _normalise(a.artist_name), _normalise(b.artist_name)
                    ).ratio()
                    if ratio >= self._threshold:
                        matches.append((a, b, round(ratio * 100)))
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

        self.progress.emit(checked if stopped else total_pairs, max(total_pairs, 1))
        if stopped:
            logger.info(f"Artist fuzzy match scan stopped by user after {checked:,} pairs")
        return matches


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

            # Radio buttons for artist selection
            radio_a = QRadioButton(artist_a.artist_name)
            radio_a.artist = artist_a
            radio_b = QRadioButton(artist_b.artist_name)
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
        btn_merge = QPushButton("Merge Checked Pairs")
        btn_merge.clicked.connect(self._perform_merge)
        btn_box.addWidget(btn_merge)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        layout.addLayout(btn_box)

    def _perform_merge(self) -> None:
        """Only merge checked pairs with user-selected canonical artist."""
        success_count = 0
        for idx, (chk_merge, radio_a, radio_b) in enumerate(self.match_widgets):
            if not chk_merge.isChecked():
                continue  # Skip unchecked pairs

            # Determine which artist to keep
            if radio_a.isChecked():
                old_artist = radio_b.artist
                new_artist = radio_a.artist
            else:
                old_artist = radio_a.artist
                new_artist = radio_b.artist

            try:
                logger.info(
                    f"Merging {old_artist.artist_name} (ID: {old_artist.artist_id}) into {new_artist.artist_name} (ID: {new_artist.artist_id})"
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
            except Exception as e:
                logger.error(
                    f"Failed to merge {old_artist.artist_name} → {new_artist.artist_name}: {e}"
                )

        if success_count > 0:
            QMessageBox.information(
                self,
                "Merge Complete",
                f"Successfully merged {success_count}/{len(self.match_widgets)} pairs",
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "No Merges",
                "No pairs were merged (none checked or errors occurred)",
            )
