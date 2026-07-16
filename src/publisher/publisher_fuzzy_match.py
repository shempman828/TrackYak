from typing import Any, List

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
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

from src.core.logger_config import logger
from src.core.status_utility import show_status_message

# Longest a publisher name is allowed to render in the match list before
# being elided, so one very long name can't blow out the column width
# every row aligns to.
_MAX_NAME_CHARS = 40


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class _PublisherMergeWorker(QThread):
    """Runs the checked merges off the UI thread so a large batch doesn't
    freeze the dialog, reporting progress as each pair completes."""

    progress = Signal(int, int)  # current, total
    finished = Signal(int, int, list)  # success_count, total, error_messages

    def __init__(self, controller, jobs: list, parent=None):
        super().__init__(parent)
        self.controller = controller
        # jobs: list of (old_publisher, new_publisher)
        self.jobs = jobs

    def run(self) -> None:
        total = len(self.jobs)
        success_count = 0
        errors: list[str] = []

        for idx, (old_publisher, new_publisher) in enumerate(self.jobs):
            try:
                logger.info(
                    f"Merging {old_publisher.publisher_name} (ID: {old_publisher.publisher_id}) "
                    f"into {new_publisher.publisher_name} (ID: {new_publisher.publisher_id})"
                )
                merged = self.controller.merge.merge_entities(
                    "Publisher",
                    old_publisher.publisher_id,
                    new_publisher.publisher_id,
                )
                if merged:
                    success_count += 1
                else:
                    msg = (
                        f"Failed to merge {old_publisher.publisher_name} → "
                        f"{new_publisher.publisher_name}: merge_entities returned False"
                    )
                    logger.error(msg)
                    errors.append(msg)
            except Exception as e:
                msg = (
                    f"Failed to merge {old_publisher.publisher_name} → "
                    f"{new_publisher.publisher_name}: {e}"
                )
                logger.error(msg)
                errors.append(msg)

            self.progress.emit(idx + 1, total)

        self.finished.emit(success_count, total, errors)


# Fuzzy Match Dialog
# -------------------------
class PublisherFuzzyMatchDialog(QDialog):
    """Dialog to display fuzzy publisher matches and allow merging."""

    def __init__(self, matches: List[tuple], controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.matches = sorted(
            matches, key=lambda x: x[2], reverse=True
        )  # x[2] is the score
        self.setWindowTitle("Merge Publishers")
        self.setMinimumSize(600, 400)
        self.init_ui()

    @staticmethod
    def _display_name(name: str) -> str:
        """Elide overly long publisher names so one long name can't blow out
        the column width every row's radio buttons align to."""
        if len(name) <= _MAX_NAME_CHARS:
            return name
        return name[: _MAX_NAME_CHARS - 1].rstrip() + "…"

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel(
            "✔ Check pairs to merge | 🅐🅑 Select which publisher to keep | ✖ Leave unchecked to ignore"
        )
        layout.addWidget(lbl_instructions)

        # Scrollable match list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        # A single grid (rather than one QHBoxLayout per pair) keeps the
        # checkbox/radio/score columns aligned across rows regardless of
        # how long any individual publisher name is.
        grid = QGridLayout(content)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        # Add each match pair with controls. Each pair occupies two grid
        # rows: the controls, then a separator line before the next pair.
        self.match_widgets = []
        for i, (publisher_a, publisher_b, score) in enumerate(self.matches):
            row = i * 2

            # Checkbox to enable/disable merging for this pair
            chk_merge = QCheckBox()
            chk_merge.setChecked(False)  # Default to unchecked
            grid.addWidget(chk_merge, row, 0)

            # Radio buttons for publisher selection
            radio_a = QRadioButton(self._display_name(publisher_a.publisher_name))
            radio_a.setToolTip(publisher_a.publisher_name)
            radio_a.publisher = publisher_a
            radio_b = QRadioButton(self._display_name(publisher_b.publisher_name))
            radio_b.setToolTip(publisher_b.publisher_name)
            radio_b.publisher = publisher_b
            radio_a.setChecked(True)  # Default to first publisher

            grid.addWidget(radio_a, row, 1)
            grid.addWidget(radio_b, row, 2)
            grid.addWidget(QLabel(f"Similarity: {score}%"), row, 3)

            if i < len(self.matches) - 1:
                separator = QFrame()
                separator.setFrameShape(QFrame.HLine)
                separator.setFrameShadow(QFrame.Sunken)
                grid.addWidget(separator, row + 1, 0, 1, 4)

            self.match_widgets.append((chk_merge, radio_a, radio_b))

        grid.setColumnStretch(4, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Progress bar (hidden until a merge is running)
        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.hide()
        layout.addWidget(self._status_label)

        # Action buttons
        btn_box = QHBoxLayout()
        self.btn_merge = QPushButton("Merge Checked Pairs")
        self.btn_merge.clicked.connect(self._perform_merge)
        btn_box.addWidget(self.btn_merge)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        layout.addLayout(btn_box)

        self._worker: _PublisherMergeWorker | None = None

        self._autosize(content)

    def _autosize(self, content: QWidget) -> None:
        """Grow the dialog to fit the match grid, within reason.

        A handful of matches shouldn't leave most of the window empty, and
        hundreds of matches shouldn't blow the dialog past the screen — so
        the natural content size is clamped to a fraction of the available
        screen space, with the configured minimum as a floor.
        """
        hint = content.sizeHint()

        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()

        # Extra room for the scrollbar plus the instructions/buttons/margins
        # that sit outside the scrolled grid itself.
        width = hint.width() + 60
        height = hint.height() + 160

        width = max(self.minimumWidth(), min(width, int(available.width() * 0.9)))
        height = max(self.minimumHeight(), min(height, int(available.height() * 0.9)))

        self.resize(width, height)

    def _perform_merge(self) -> None:
        """Kick off a background merge of the checked pairs with the
        user-selected canonical publisher, showing progress as it runs."""
        jobs = []
        for chk_merge, radio_a, radio_b in self.match_widgets:
            if not chk_merge.isChecked():
                continue  # Skip unchecked pairs

            # Determine which publisher to keep
            if radio_a.isChecked():
                old_publisher = radio_b.publisher
                new_publisher = radio_a.publisher
            else:
                old_publisher = radio_a.publisher
                new_publisher = radio_b.publisher

            jobs.append((old_publisher, new_publisher))

        if not jobs:
            show_status_message(
                self, "No pairs were merged (none checked or errors occurred)"
            )
            return

        self.btn_merge.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._progress.setRange(0, len(jobs))
        self._progress.setValue(0)
        self._progress.show()
        self._status_label.setText(f"Merging 0/{len(jobs)}…")
        self._status_label.show()

        self._worker = _PublisherMergeWorker(self.controller, jobs, parent=self)
        self._worker.progress.connect(self._on_merge_progress)
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.start()

    def _on_merge_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)
        self._status_label.setText(f"Merging {current}/{total}…")

    def _on_merge_finished(
        self, success_count: int, total: int, errors: list
    ) -> None:
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
