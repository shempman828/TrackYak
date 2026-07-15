# track_edit_advanced.py
"""
AdvancedTab — wraps FieldFormTab("Advanced") and adds two action buttons:

  • Copy to Clipboard — serialises current field values to the clipboard.
  • Analyse Audio    — runs BatchAnalysisScheduler on the track(s) being
                       edited, wiring its Qt signals back to this widget for
                       live progress feedback.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.statistics.analysis_utility import BatchAnalysisScheduler, analysis_cache
from src.core.logger_config import logger
from src.track.track_edit_basetab import _BaseTab
from src.track.track_edit_fieldform import FieldFormTab


class AdvancedTab(_BaseTab):
    """
    The Advanced tab panel.

    Layout
    ------
    ┌─────────────────────────────────┐
    │  FieldFormTab("Advanced")       │  ← all the normal fields
    ├─────────────────────────────────┤
    │  [Copy to Clipboard] [Analyse]  │  ← action toolbar
    │  <status label>                 │
    └─────────────────────────────────┘
    """

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)

        self._inner = FieldFormTab("Advanced", tracks, controller)
        self._scheduler: BatchAnalysisScheduler | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._inner, stretch=1)
        layout.addWidget(self._build_toolbar())

    # ── Toolbar construction ──────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 2)
        vbox.setSpacing(2)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._copy_btn = QPushButton("Copy to Clipboard")
        self._copy_btn.setToolTip("Copy all Advanced field values to the clipboard")
        self._copy_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._copy_btn.clicked.connect(self._on_copy)

        self._analyse_btn = QPushButton("Analyse Audio")
        self._analyse_btn.setToolTip(
            "Run audio analysis on the selected track(s).\n"
            "Tracks already cached are skipped; hold Shift to force re-analyse."
        )
        self._analyse_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._analyse_btn.clicked.connect(self._on_analyse)

        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._analyse_btn)
        btn_row.addStretch()

        # Status label — hidden until analysis starts
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignLeft)
        self._status_label.hide()

        vbox.addLayout(btn_row)
        vbox.addWidget(self._status_label)
        return container

    # ── _BaseTab protocol ─────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self._inner.load(tracks)

    def collect_changes(self) -> dict:
        return self._inner.collect_changes()

    # ── Copy to clipboard ─────────────────────────────────────────────────

    def _on_copy(self):
        """Serialise current Advanced field values and put them on the clipboard."""
        try:
            changes = self._inner.collect_changes()
            if not changes:
                QMessageBox.information(
                    self, "Copy to Clipboard", "No Advanced fields have values to copy."
                )
                return

            lines = [f"{field}: {value}" for field, value in changes.items()]
            QApplication.clipboard().setText("\n".join(lines))
            logger.info(f"AdvancedTab: copied {len(lines)} field(s) to clipboard")
        except Exception as e:
            logger.error(f"AdvancedTab clipboard copy failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Copy Error", f"Failed to copy:\n{e}")

    # ── Audio analysis ────────────────────────────────────────────────────

    def _on_analyse(self):
        """
        Start BatchAnalysisScheduler for the tracks being edited.

        Shift+click clears those track IDs from the analysis cache first,
        forcing a full re-analyse even if they were already processed.
        """
        if self._scheduler and self._scheduler.is_running:
            # Button acts as a stop button while a run is in progress
            self._scheduler.stop()
            self._set_status("Stopping…")
            return

        modifiers = QApplication.keyboardModifiers()
        force = bool(modifiers & Qt.ShiftModifier)

        if force:
            for track in self.tracks:
                analysis_cache.remove(track.track_id)
            logger.info(
                f"AdvancedTab: forced re-analyse — cleared cache for "
                f"{len(self.tracks)} track(s)"
            )

        # Check whether everything is already cached (and we're not forcing)
        if not force:
            uncached = [
                t for t in self.tracks if not analysis_cache.is_analysed(t.track_id)
            ]
            if not uncached:
                QMessageBox.information(
                    self,
                    "Analyse Audio",
                    "All selected track(s) are already analysed.\n\n"
                    "Shift+click 'Analyse Audio' to force re-analysis.",
                )
                return

        self._scheduler = BatchAnalysisScheduler(self.controller, num_workers=2)

        # Wire signals — all delivered on the main thread via Qt's queued
        # connection, so it's safe to touch widgets directly in these slots.
        self._scheduler.signals.track_done.connect(self._on_track_done)
        self._scheduler.signals.batch_done.connect(self._on_batch_done)
        self._scheduler.signals.all_done.connect(self._on_all_done)
        self._scheduler.signals.error.connect(self._on_analysis_error)

        self._analyse_btn.setText("Stop Analysis")
        self._set_status(f"Queuing {len(self.tracks)} track(s)…")
        self._scheduler.start(self.tracks)

    # ── Scheduler signal handlers ─────────────────────────────────────────

    @Slot(int, dict)
    def _on_track_done(self, track_id: int, metadata: dict):
        if self._scheduler:
            done, total = self._scheduler.progress
            self._set_status(f"Analysed {done} / {total} track(s)…")

    @Slot(int, int)
    def _on_batch_done(self, completed: int, total: int):
        self._set_status(f"Analysed {completed} / {total} track(s)…")

    @Slot(int)
    def _on_all_done(self, total: int):
        self._analyse_btn.setText("Analyse Audio")
        self._set_status(f"Analysis complete — {total} track(s) processed.")
        logger.info(f"AdvancedTab: analysis finished ({total} track(s))")

    @Slot(int, str)
    def _on_analysis_error(self, track_id: int, message: str):
        logger.error(f"AdvancedTab: analysis error for track {track_id}: {message}")
        # Don't interrupt the run with a modal — just update the status label.
        # Fatal errors will surface in the all_done summary above.
        self._set_status(f"Error on track {track_id} — see log for details.")

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_label.setText(text)
        self._status_label.show()
