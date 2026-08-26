"""
fuzzy_match_dialog.py

Shared scaffold for "scan for fuzzy-duplicate entities, let the user pick
which pairs to merge and which name to keep" dialogs (currently used by
artist and publisher dedup). Finding candidate pairs stays entity-specific
(different blocking/similarity logic per entity type) and lives upstream of
this dialog; so does each dialog's match-row UI, since that differs enough
between entities (role-count display vs. name elision/autosize) that forcing
a shared layout isn't worth it. What's shared is the actual merge
orchestration: building jobs from the checked rows, running them off the UI
thread, and reporting progress/completion.
"""

from typing import Callable, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from src.common.cancellable_worker import CancellableWorker
from src.core.logger_config import logger


class BaseMergeWorker(CancellableWorker):
    """Runs checked entity-merge pairs off the UI thread so a large batch
    doesn't freeze the dialog, reporting progress as each pair completes.

    `on_pair_merged(old_entity, new_entity)`, if given, runs immediately
    after a successful merge for entity-specific side effects (e.g. artist
    dedup recording the old name as an alias of the survivor) -- if it
    raises, the pair is counted as failed even though the merge itself
    succeeded, matching how the original artist-only worker this was
    extracted from treated alias-creation failures.
    """

    progress = Signal(int, int)  # current, total
    finished = Signal(int, int, list)  # success_count, total, error_messages

    def __init__(
        self,
        controller,
        entity_type: str,
        id_attr: str,
        name_attr: str,
        jobs: list,
        on_pair_merged: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.entity_type = entity_type
        self.id_attr = id_attr
        self.name_attr = name_attr
        self.jobs = jobs  # list of (old_entity, new_entity)
        self.on_pair_merged = on_pair_merged

    def run(self) -> None:
        total = len(self.jobs)
        success_count = 0
        errors: list[str] = []

        try:
            for idx, (old_entity, new_entity) in enumerate(self.jobs):
                old_name = getattr(old_entity, self.name_attr)
                new_name = getattr(new_entity, self.name_attr)
                old_id = getattr(old_entity, self.id_attr)
                new_id = getattr(new_entity, self.id_attr)
                try:
                    logger.info(
                        f"Merging {old_name} (ID: {old_id}) into {new_name} (ID: {new_id})"
                    )
                    merged = self.controller.merge.merge_entities(
                        self.entity_type, old_id, new_id
                    )
                    if not merged:
                        msg = (
                            f"Failed to merge {old_name} → {new_name}: "
                            "merge_entities returned False"
                        )
                        logger.error(msg)
                        errors.append(msg)
                    else:
                        if self.on_pair_merged:
                            self.on_pair_merged(old_entity, new_entity)
                        success_count += 1
                except Exception as e:
                    # Intentional broad boundary catch: this runs on a QThread
                    # and merge_entities (or a merged-pair side effect) can
                    # fail in many ways per pair -- one bad pair must not kill
                    # the whole batch, so log and keep going.
                    msg = f"Failed to merge {old_name} → {new_name}: {e}"
                    logger.exception(msg)
                    errors.append(msg)

                self.progress.emit(idx + 1, total)
        finally:
            self._release_db_session()

        self.finished.emit(success_count, total, errors)


class BaseFuzzyMatchDialog(QDialog):
    """Shared merge orchestration for fuzzy-duplicate dialogs.

    Subclasses must:
      - set `_ENTITY_TYPE` / `_ID_ATTR` / `_NAME_ATTR` class attributes
      - implement `init_ui()`, populating `self.match_widgets` as a list of
        (checkbox, radio_a, radio_b) tuples with `radio_a.entity` /
        `radio_b.entity` set to the entity each radio represents, wiring
        `self.btn_merge`/`self.btn_cancel` to `self._perform_merge`/
        `self.reject`, and creating `self._progress` (QProgressBar) /
        `self._status_label` (QLabel), both hidden until a merge starts
      - implement `_notify_no_jobs()` to report "nothing was checked"
        however that dialog normally surfaces messages
      - optionally override `_on_pair_merged()` for a per-pair side effect
        after a successful merge (e.g. alias creation)
    """

    _ENTITY_TYPE: str = ""
    _ID_ATTR: str = ""
    _NAME_ATTR: str = ""

    def __init__(self, matches: List[tuple], controller, title: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.matches = sorted(
            matches, key=lambda x: x[2], reverse=True
        )  # x[2] is the score
        self.setWindowTitle(title)
        self.setMinimumSize(600, 400)
        self.match_widgets = []
        self._worker: Optional[BaseMergeWorker] = None
        self.init_ui()

    def init_ui(self) -> None:
        raise NotImplementedError

    def _notify_no_jobs(self) -> None:
        raise NotImplementedError

    def _on_pair_merged(self, old_entity, new_entity) -> None:
        """Optional per-pair side effect after a successful merge. No-op by default."""

    def _perform_merge(self) -> None:
        """Kick off a background merge of the checked pairs with the
        user-selected canonical entity, showing progress as it runs."""
        jobs = []
        for chk_merge, radio_a, radio_b in self.match_widgets:
            if not chk_merge.isChecked():
                continue  # Skip unchecked pairs

            # Determine which entity to keep
            if radio_a.isChecked():
                old_entity, new_entity = radio_b.entity, radio_a.entity
            else:
                old_entity, new_entity = radio_a.entity, radio_b.entity

            jobs.append((old_entity, new_entity))

        if not jobs:
            self._notify_no_jobs()
            return

        self.btn_merge.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._progress.setRange(0, len(jobs))
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText(f"Merging 0/{len(jobs)}…")
        self._status_label.show()

        self._worker = BaseMergeWorker(
            self.controller,
            self._ENTITY_TYPE,
            self._ID_ATTR,
            self._NAME_ATTR,
            jobs,
            on_pair_merged=self._on_pair_merged,
            parent=self,
        )
        self._worker.progress.connect(self._on_merge_progress)
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.start()

    def _on_merge_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)
        self._status_label.setText(f"Merging {current}/{total}…")

    def _on_merge_finished(self, success_count: int, total: int, errors: list) -> None:
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
