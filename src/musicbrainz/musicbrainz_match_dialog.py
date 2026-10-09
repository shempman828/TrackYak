"""MusicBrainz picker and import dialogs that run their network calls on a MusicBrainzWorker."""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import warnings

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import MBCandidate
from src.musicbrainz.musicbrainz_worker import MusicBrainzWorker

# Strong refs to detached, still-running workers: without them the QThread is garbage-
# collected with its dialog mid-run ("QThread: Destroyed while thread is still running").
_DETACHED_WORKERS: set[MusicBrainzWorker] = set()


def _forget_worker(worker: MusicBrainzWorker) -> None:
    """Drop a detached worker's parked reference and schedule its deletion once."""
    # Membership check guards against a second emission calling deleteLater twice.
    if worker in _DETACHED_WORKERS:
        _DETACHED_WORKERS.discard(worker)
        worker.deleteLater()


def _detach_running_worker(worker: MusicBrainzWorker | None) -> None:
    """Detach a still-running worker from its closing dialog and let it finish on its own."""
    # The network call cannot be interrupted, and wait() would freeze the UI.
    if worker is None or not worker.isRunning():
        return
    # A signal with no current receivers both warns and raises here, so
    # mute the warning and swallow the RuntimeError per signal.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for signal in (worker.finished, worker.error, worker.progress, worker.status):
            with contextlib.suppress(RuntimeError):
                signal.disconnect()
    worker.setParent(None)
    _DETACHED_WORKERS.add(worker)
    worker.finished.connect(lambda *_: _forget_worker(worker))
    worker.error.connect(lambda *_: _forget_worker(worker))
    # run() may have returned between the isRunning() check above and the
    # connects, in which case finished/error already fired into the void --
    # release the parked reference now rather than leaking it forever.
    if worker.isFinished():
        _forget_worker(worker)


class MusicBrainzMatchDialog(QDialog):
    """Run `search_call`, let the user pick one MBCandidate or skip, then run the optional `complete_call` on it."""

    def __init__(self, entity_label: str, search_call: Callable[[], list[MBCandidate]], complete_call: Callable[[MBCandidate], MBCandidate] | None = None, parent=None):
        super().__init__(parent)
        self._entity_label = entity_label
        self._search_call = search_call
        self._complete_call = complete_call
        self._candidates: list[MBCandidate] = []
        self._result_enrichment: dict | None = None
        self._result_candidate: MBCandidate | None = None
        self._pending_candidate: MBCandidate | None = None  # the pick sent to complete_call
        self._worker: MusicBrainzWorker | None = None

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
        self.list_widget.setAccessibleName("MusicBrainz matches")
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_accept())
        layout.addWidget(self.list_widget, stretch=1)

        # Shown only when the selected row has `alternates` (other pressings of the same group).
        variant_row = QHBoxLayout()
        self.variant_label = QLabel("&Pressing:")
        variant_row.addWidget(self.variant_label)
        self.variant_combo = QComboBox()
        self.variant_combo.setAccessibleName("Pressing")
        self.variant_label.setBuddy(self.variant_combo)
        variant_row.addWidget(self.variant_combo, stretch=1)
        self.variant_label.setVisible(False)
        self.variant_combo.setVisible(False)
        layout.addLayout(variant_row)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Use Selected Match")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Skip")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.retry_button = self.buttons.addButton("Retry", QDialogButtonBox.ActionRole)
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self._on_retry)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        button_row.addWidget(self.buttons)
        layout.addLayout(button_row)

    # ------------------------------------------------------------------
    # Search step
    # ------------------------------------------------------------------

    def _start_search(self):
        """Start the search on a new worker."""
        self._worker = MusicBrainzWorker(self._search_call, self)
        self._worker.finished.connect(self._on_search_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_search_finished(self, candidates: list[MBCandidate]):
        self._candidates = candidates
        self.progress_bar.hide()
        self.list_widget.clear()

        if not candidates:
            self.status_label.setText(f"No MusicBrainz matches found for {self._entity_label}.")
            return

        noun = "match" if len(candidates) == 1 else "matches"
        self.status_label.setText(f"Found {len(candidates)} possible {noun} for {self._entity_label}:")
        for candidate in candidates:
            item = QListWidgetItem(candidate.label)
            item.setData(Qt.UserRole, candidate)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        self._autosize_to_content()

    def _autosize_to_content(self) -> None:
        """Widen the dialog to fit the longest candidate row, clamped to 90% of the screen."""
        if self.list_widget.count() == 0:
            return

        content = self.list_widget.sizeHintForColumn(0)
        frame = 2 * self.list_widget.frameWidth()
        scrollbar = self.list_widget.verticalScrollBar().sizeHint().width()
        margins = self.layout().contentsMargins()
        width = content + frame + scrollbar + margins.left() + margins.right() + 24

        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            width = min(width, int(screen.availableGeometry().width() * 0.9))
        width = max(self.minimumWidth(), width)

        self.resize(width, self.height())

    def _on_error(self, message: str):
        self.progress_bar.hide()
        self.status_label.setText(f"MusicBrainz lookup failed: {message}")
        self.retry_button.setVisible(True)
        logger.error(f"MusicBrainz lookup failed: {message}")

    def _on_retry(self):
        """Run the search again after a failure."""
        self.retry_button.setVisible(False)
        self.status_label.setText(f"Searching MusicBrainz for {self._entity_label}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        _detach_running_worker(self._worker)
        self._start_search()

    def _on_selection_changed(self):
        items = self.list_widget.selectedItems()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(items))

        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        candidate: MBCandidate | None = items[0].data(Qt.UserRole) if items else None
        has_variants = bool(candidate and candidate.alternates)
        if has_variants:
            for variant in [candidate, *candidate.alternates]:
                self.variant_combo.addItem(variant.label, variant)
            self.variant_combo.setCurrentIndex(0)
        self.variant_label.setVisible(has_variants)
        self.variant_combo.setVisible(has_variants)
        self.variant_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Accept step (optional follow-up enrichment call)
    # ------------------------------------------------------------------

    def _on_accept(self):
        items = self.list_widget.selectedItems()
        if not items:
            return
        candidate: MBCandidate = items[0].data(Qt.UserRole)
        # Check the combo's count, not isVisible(): visibility needs a shown ancestor chain.
        if self.variant_combo.count() > 0:
            variant = self.variant_combo.currentData()
            if variant is not None:
                candidate = variant

        if self._complete_call is None:
            self._result_enrichment = candidate.enrichment
            self._result_candidate = candidate
            self.accept()
            return

        # Keep Skip enabled so a slow follow-up can be abandoned with the mouse.
        self._pending_candidate = candidate
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.list_widget.setEnabled(False)
        self.variant_combo.setEnabled(False)
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
        # Fall back to the exact pick (including a chosen pressing), not the list row.
        candidate = self._pending_candidate
        if candidate is not None:
            self._result_enrichment = candidate.enrichment
            self._result_candidate = candidate
        self.accept()

    def result_enrichment(self) -> dict | None:
        """The picked candidate's enrichment dict, or None if skipped."""
        return self._result_enrichment

    def result_candidate(self) -> MBCandidate | None:
        """The full picked candidate, including any `relations` that complete_call filled."""
        return self._result_candidate

    def candidate_count(self) -> int:
        """How many candidates the search found, to tell an empty search from a skip."""
        return len(self._candidates)

    def reject(self):
        _detach_running_worker(self._worker)
        super().reject()

    def closeEvent(self, event):
        _detach_running_worker(self._worker)
        super().closeEvent(event)


class MusicBrainzImportDialog(QDialog):
    """Run `fetch_call` for an already-known MBID on a worker and return its result."""

    # supports_progress=True: fetch_call takes (progress_callback, status_callback) positionally.
    # Below this many steps, keep the indeterminate spinner instead of a counter.
    _PROGRESS_DISPLAY_THRESHOLD = 5

    def __init__(self, entity_label: str, fetch_call: Callable[..., MBCandidate], parent=None, supports_progress: bool = False):
        super().__init__(parent)
        self._fetch_call = fetch_call
        self._supports_progress = supports_progress
        self._entity_label = entity_label
        self._result_candidate = None
        self._worker: MusicBrainzWorker | None = None

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
        self.retry_button = self.buttons.addButton("Retry", QDialogButtonBox.ActionRole)
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self._on_retry)
        self.buttons.rejected.connect(self.reject)
        button_row.addWidget(self.buttons)
        layout.addLayout(button_row)

        self._start_fetch()

    def _start_fetch(self):
        """Start fetch_call on a new worker."""
        # Placeholder call first: the closure must reference the worker's own emit methods.
        worker = MusicBrainzWorker(lambda: None, self)
        if self._supports_progress:
            worker._call = lambda: self._fetch_call(worker.progress.emit, worker.status.emit)
            worker.progress.connect(self._on_progress)
            worker.status.connect(self._on_status)
        else:
            worker._call = lambda: self._fetch_call()
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        self._worker = worker
        worker.start()

    def _on_progress(self, current: int, total: int):
        if total <= self._PROGRESS_DISPLAY_THRESHOLD:
            return
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

    def _on_status(self, message: str):
        self.status_label.setText(f"{message} — {self._entity_label}")

    def _on_finished(self, candidate):
        self._result_candidate = candidate
        self.accept()

    def _on_error(self, message: str):
        logger.warning(f"MusicBrainz import failed: {message}")
        self.progress_bar.hide()
        self.status_label.setText(f"MusicBrainz import failed: {message}")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Close")
        self.retry_button.setVisible(True)

    def _on_retry(self):
        """Run the fetch again after a failure."""
        self.retry_button.setVisible(False)
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        self.status_label.setText(f"Importing details for {self._entity_label}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        _detach_running_worker(self._worker)
        self._start_fetch()

    def result_candidate(self):
        """Whatever fetch_call returned, or None if it failed or was cancelled."""
        return self._result_candidate

    def reject(self):
        _detach_running_worker(self._worker)
        super().reject()

    def closeEvent(self, event):
        _detach_running_worker(self._worker)
        super().closeEvent(event)
