"""
musicbrainz_match_dialog.py

Generic "here are N possible MusicBrainz matches, pick one or skip" picker.
Simpler single-column cousin of the pairwise merge-candidate dialogs in
artist_fuzzy_match.py / publisher_fuzzy_match.py: one row per candidate,
no radio-pair/merge semantics, just OK or Skip.

Runs the search (and, for entities that need it, a short follow-up lookup
after the user picks a candidate) on a MusicBrainzWorker so the UI never
blocks on the network call.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
)

from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import MBCandidate
from src.musicbrainz.musicbrainz_worker import MusicBrainzWorker


class MusicBrainzMatchDialog(QDialog):
    """
    Args:
        entity_label: short description shown in the status text, e.g.
            "artist 'Radiohead'".
        search_call: zero-arg callable returning List[MBCandidate].
        complete_call: optional callable(MBCandidate) -> MBCandidate, run
            once after the user picks a row, for enrichment fields the
            search endpoint doesn't return (e.g. artist links, track ISRC).

    After exec(), call `result_enrichment()` — returns the chosen
    candidate's enrichment dict, or None if the user skipped/cancelled.
    """

    def __init__(
        self,
        entity_label: str,
        search_call: Callable[[], List[MBCandidate]],
        complete_call: Optional[Callable[[MBCandidate], MBCandidate]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._entity_label = entity_label
        self._search_call = search_call
        self._complete_call = complete_call
        self._candidates: List[MBCandidate] = []
        self._result_enrichment: Optional[Dict] = None
        self._result_candidate: Optional[MBCandidate] = None
        self._worker: Optional[MusicBrainzWorker] = None

        self.setWindowTitle("MusicBrainz Lookup")
        self.setMinimumSize(520, 420)
        self._build_ui()
        self._start_search()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.status_label = QLabel(f"Searching MusicBrainz for {self._entity_label}...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self.progress_bar)

        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_accept())
        layout.addWidget(self.list_widget, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Use Selected Match")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Skip")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        button_row.addWidget(self.buttons)
        layout.addLayout(button_row)

    # ------------------------------------------------------------------
    # Search step
    # ------------------------------------------------------------------

    def _start_search(self):
        self._worker = MusicBrainzWorker(self._search_call, self)
        self._worker.finished.connect(self._on_search_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_search_finished(self, candidates: List[MBCandidate]):
        self._candidates = candidates
        self.progress_bar.hide()
        self.list_widget.clear()

        if not candidates:
            self.status_label.setText(
                f"No MusicBrainz matches found for {self._entity_label}."
            )
            return

        self.status_label.setText(
            f"Found {len(candidates)} possible match(es) for {self._entity_label}:"
        )
        for candidate in candidates:
            item = QListWidgetItem(candidate.label)
            item.setData(Qt.UserRole, candidate)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)

    def _on_error(self, message: str):
        self.progress_bar.hide()
        self.status_label.setText(f"MusicBrainz lookup failed: {message}")
        logger.error(f"MusicBrainz lookup failed: {message}")

    def _on_selection_changed(self):
        has_selection = bool(self.list_widget.selectedItems())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Accept step (optional follow-up enrichment call)
    # ------------------------------------------------------------------

    def _on_accept(self):
        items = self.list_widget.selectedItems()
        if not items:
            return
        candidate: MBCandidate = items[0].data(Qt.UserRole)

        if self._complete_call is None:
            self._result_enrichment = candidate.enrichment
            self._result_candidate = candidate
            self.accept()
            return

        self.buttons.setEnabled(False)
        self.list_widget.setEnabled(False)
        self.status_label.setText("Fetching additional details...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        self._worker = MusicBrainzWorker(lambda: self._complete_call(candidate), self)
        self._worker.finished.connect(self._on_complete_finished)
        self._worker.error.connect(self._on_complete_error)
        self._worker.start()

    def _on_complete_finished(self, candidate: MBCandidate):
        self._result_enrichment = candidate.enrichment
        self._result_candidate = candidate
        self.accept()

    def _on_complete_error(self, message: str):
        # Follow-up enrichment is best-effort -- fall back to whatever the
        # search step already found rather than blocking the whole match.
        logger.warning(f"MusicBrainz enrichment follow-up failed: {message}")
        items = self.list_widget.selectedItems()
        if items:
            candidate = items[0].data(Qt.UserRole)
            self._result_enrichment = candidate.enrichment
            self._result_candidate = candidate
        self.accept()

    def result_enrichment(self) -> Optional[Dict]:
        return self._result_enrichment

    def result_candidate(self) -> Optional[MBCandidate]:
        """The full picked candidate, including any relational data
        (aliases, places, group membership) a `complete_call` populated on
        `candidate.relations`. Most callers only need `result_enrichment()`;
        this is for entities that follow up with a review dialog."""
        return self._result_candidate

    def candidate_count(self) -> int:
        """How many candidates the search actually found -- lets a caller
        distinguish "search came back genuinely empty" (e.g. a misspelled
        artist name) from "the user just skipped a non-empty list", after
        exec() returns something other than Accepted."""
        return len(self._candidates)


class MusicBrainzImportDialog(QDialog):
    """
    Single-step sibling of MusicBrainzMatchDialog for when the MBID is
    already known (e.g. re-fetching an artist that was matched previously)
    -- no search, no picker list, just run `fetch_call` on a worker and
    report back the resulting candidate.

    After exec(), call `result_candidate()` -- returns whatever `fetch_call`
    returned (an `MBCandidate` for the simple re-fetch case, or any other
    payload for callers reusing this dialog for a different fetch shape),
    or None if the fetch failed or the user cancelled.

    Pass `supports_progress=True` for a `fetch_call` that accepts a single
    `progress_callback(current, total)` positional argument (e.g.
    `fetch_release_detail`'s recording-location resolution) -- the dialog
    then switches its progress bar from an indeterminate spinner to a
    determinate "(n of total)" counter once more than 5 steps are queued,
    so a long-running fetch doesn't look hung. Callers that don't need this
    can leave it False and keep passing a plain zero-arg `fetch_call`.
    """

    # Below this many queued steps, the determinate counter isn't worth
    # showing -- a couple of quick lookups just keep the plain spinner.
    _PROGRESS_DISPLAY_THRESHOLD = 5

    def __init__(
        self,
        entity_label: str,
        fetch_call: Callable[..., MBCandidate],
        parent=None,
        supports_progress: bool = False,
    ):
        super().__init__(parent)
        self._fetch_call = fetch_call
        self._supports_progress = supports_progress
        self._entity_label = entity_label
        self._result_candidate = None
        self._worker: Optional[MusicBrainzWorker] = None

        self.setWindowTitle("MusicBrainz Import")
        self.setMinimumSize(420, 140)

        layout = QVBoxLayout(self)
        self.status_label = QLabel(f"Importing details for {entity_label}...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self.progress_bar)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self.reject)
        button_row.addWidget(self.buttons)
        layout.addLayout(button_row)

        # The worker is constructed with a placeholder call so `_call` can
        # be a closure over `self._worker.progress.emit` -- the worker has
        # to exist before that closure can reference it.
        self._worker = MusicBrainzWorker(lambda: None, self)
        if supports_progress:
            self._worker._call = lambda: fetch_call(self._worker.progress.emit)
            self._worker.progress.connect(self._on_progress)
        else:
            self._worker._call = lambda: fetch_call()
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, current: int, total: int):
        if total <= self._PROGRESS_DISPLAY_THRESHOLD:
            return
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.status_label.setText(
            f"Resolving recording locations for {self._entity_label}… "
            f"({current} of {total})"
        )

    def _on_finished(self, candidate):
        self._result_candidate = candidate
        self.accept()

    def _on_error(self, message: str):
        logger.warning(f"MusicBrainz import failed: {message}")
        self.progress_bar.hide()
        self.status_label.setText(f"MusicBrainz import failed: {message}")

    def result_candidate(self):
        return self._result_candidate
