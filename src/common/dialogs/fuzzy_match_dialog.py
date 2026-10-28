"""Shared merge orchestration for the fuzzy-duplicate-scan dialogs (checked rows -> background merge)."""

from typing import NamedTuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.common.cancellable_worker import CancellableWorker
from src.common.dialogs.conflict_resolution import PairConflictDialog, get_entity_conflicts
from src.common.dismissed_duplicates import DEFAULT_DISMISSED_DUPLICATES_PATH, dismiss_pair, load_dismissed_pairs, normalize_pair
from src.foundation.logger_config import logger


class MergeJob(NamedTuple):
    """One pair to merge, as plain values read on the main thread."""

    old_id: int
    old_name: str
    new_id: int
    new_name: str
    resolved_fields: dict


def make_merge_job(old_entity, new_entity, id_attr: str, name_attr: str, resolved_fields: dict) -> MergeJob:
    """Build a MergeJob from two ORM rows; call on the main thread that owns their session."""
    return MergeJob(getattr(old_entity, id_attr), getattr(old_entity, name_attr), getattr(new_entity, id_attr), getattr(new_entity, name_attr), resolved_fields)


class BaseMergeWorker(CancellableWorker):
    """Runs MergeJobs off the UI thread, reporting progress after each pair."""

    # Takes plain ids/names, never ORM rows: those belong to the main thread's
    # session, and reading an attribute expired by a commit would refresh it
    # through that session from this thread.

    progress = Signal(int, int)  # current, total
    finished = Signal(int, int, list)  # success_count, total, error_messages

    def __init__(self, controller, entity_type: str, jobs: list[MergeJob], parent=None):
        super().__init__(parent)
        self.controller = controller
        self.entity_type = entity_type
        self.jobs = list(jobs)

    def run(self) -> None:
        total = len(self.jobs)
        success_count = 0
        errors: list[str] = []

        try:
            for idx, job in enumerate(self.jobs):
                try:
                    logger.info(f"Merging {job.old_name} (ID: {job.old_id}) into {job.new_name} (ID: {job.new_id})")
                    if self.controller.merge.merge_entities(self.entity_type, job.old_id, job.new_id, job.resolved_fields):
                        success_count += 1
                    else:
                        msg = f"Failed to merge {job.old_name} → {job.new_name}: merge_entities returned False"
                        logger.error(msg)
                        errors.append(msg)
                except Exception as e:
                    # Intentional broad boundary catch: this runs on a QThread
                    # and one bad pair must not kill the whole batch.
                    msg = f"Failed to merge {job.old_name} → {job.new_name}: {e}"
                    logger.exception(msg)
                    errors.append(msg)

                self.progress.emit(idx + 1, total)
        finally:
            self._release_db_session()

        self.finished.emit(success_count, total, errors)


class BaseFuzzyMatchDialog(QDialog):
    """Shared merge orchestration for fuzzy-duplicate dialogs."""

    # Subclasses set _ENTITY_TYPE/_ID_ATTR/_NAME_ATTR and implement init_ui()
    # (fill self.match_widgets with (checkbox, radio_a, radio_b) tuples whose
    # radios carry `.entity`; create btn_merge, btn_cancel, _progress and a
    # hidden _status_label) and _notify_no_jobs(). A row "Dismiss" button
    # calls _dismiss_pair().

    _ENTITY_TYPE: str = ""
    _ID_ATTR: str = ""
    _NAME_ATTR: str = ""

    def __init__(self, matches: list[tuple], controller, title: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        dismissed = load_dismissed_pairs(DEFAULT_DISMISSED_DUPLICATES_PATH, self._ENTITY_TYPE)
        matches = [m for m in matches if normalize_pair(getattr(m[0], self._ID_ATTR), getattr(m[1], self._ID_ATTR)) not in dismissed]
        self.matches = sorted(matches, key=lambda x: x[2], reverse=True)  # x[2] is the score
        self.setWindowTitle(title)
        self.setMinimumSize(600, 400)
        self.match_widgets = []
        self._worker: BaseMergeWorker | None = None
        self.init_ui()

    def init_ui(self) -> None:
        raise NotImplementedError

    def _notify_no_jobs(self) -> None:
        raise NotImplementedError

    def _dismiss_pair(self, entity_a, entity_b, row_widgets: QWidget | list[QWidget], widgets_tuple: tuple) -> None:
        """Persist the pair as not-a-duplicate and remove its row (container or widget list) from view."""
        id_a, id_b = getattr(entity_a, self._ID_ATTR), getattr(entity_b, self._ID_ATTR)
        dismiss_pair(DEFAULT_DISMISSED_DUPLICATES_PATH, self._ENTITY_TYPE, id_a, id_b)
        if normalize_pair(id_a, id_b) not in load_dismissed_pairs(DEFAULT_DISMISSED_DUPLICATES_PATH, self._ENTITY_TYPE):
            QMessageBox.warning(self, "Not Saved", "The pair is hidden for now, but could not be saved. It will show again in the next scan.")
        if widgets_tuple in self.match_widgets:
            self.match_widgets.remove(widgets_tuple)
        widgets = row_widgets if isinstance(row_widgets, (list, tuple)) else [row_widgets]
        for widget in widgets:
            widget.setParent(None)
            widget.deleteLater()

    def _perform_merge(self) -> None:
        """Start a background merge of the checked pairs into each row's chosen survivor."""
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

        resolved_jobs = self._resolve_conflicts(jobs)
        if resolved_jobs is None:
            return  # user cancelled the whole batch from a conflict dialog

        self.btn_merge.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._progress.setRange(0, len(resolved_jobs))
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText(f"Merging 0/{len(resolved_jobs)}…")
        self._status_label.show()

        merge_jobs = [make_merge_job(old, new, self._ID_ATTR, self._NAME_ATTR, fields) for old, new, fields in resolved_jobs]
        self._worker = BaseMergeWorker(self.controller, self._ENTITY_TYPE, merge_jobs, parent=self)
        self._worker.progress.connect(self._on_merge_progress)
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.start()

    def _resolve_conflicts(self, jobs: list[tuple]) -> list[tuple] | None:
        """Let the user pick surviving values per conflicting pair; (old, new, fields) triples or None if cancelled."""
        resolved: list[tuple] = []
        skip_all = False
        for old_entity, new_entity in jobs:
            if skip_all:
                resolved.append((old_entity, new_entity, {}))
                continue

            conflicts = get_entity_conflicts(old_entity, new_entity, self._ID_ATTR)
            if not conflicts:
                resolved.append((old_entity, new_entity, {}))
                continue

            dialog = PairConflictDialog(old_entity, new_entity, self._ID_ATTR, getattr(old_entity, self._NAME_ATTR), getattr(new_entity, self._NAME_ATTR), len(resolved) + 1, len(jobs), parent=self)
            dialog.exec()

            if dialog.outcome == PairConflictDialog.CANCEL:
                return None
            if dialog.outcome == PairConflictDialog.SKIP_ALL:
                skip_all = True
                resolved.append((old_entity, new_entity, {}))
                continue

            resolved.append((old_entity, new_entity, dialog.resolved_fields()))

        return resolved

    def _on_merge_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)
        self._status_label.setText(f"Merging {current}/{total}…")

    def _on_merge_finished(self, success_count: int, total: int, errors: list) -> None:
        self._progress.hide()
        self._status_label.hide()
        self.btn_merge.setEnabled(True)
        self.btn_cancel.setEnabled(True)

        if success_count > 0:
            box = QMessageBox(QMessageBox.Information if not errors else QMessageBox.Warning, "Merge Complete", f"Successfully merged {success_count}/{total} pairs", parent=self)
        else:
            box = QMessageBox(QMessageBox.Warning, "No Merges", "No pairs were merged.", parent=self)
        if errors:
            box.setInformativeText(f"{len(errors)} pair(s) failed. See Show Details.")
            box.setDetailedText("\n".join(errors))
        box.exec()
        if success_count > 0:
            self.accept()

    def reject(self) -> None:
        # Cancel button is disabled while a merge is running, but guard
        # against Escape/close-button closing the dialog mid-merge anyway.
        if self._worker is not None and self._worker.isRunning():
            return
        super().reject()
