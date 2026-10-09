# track_edit_advanced.py
"""AdvancedTab: the Advanced fields plus copy, write-tags, analyze and delete actions."""

from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from src.analysis.batch_analysis_scheduler import BatchAnalysisScheduler
from src.common.dialogs.delete_confirmation import confirm_delete_with_file_option
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.metadata.writers.metadata_writer import MetadataWriter
from src.track.edit.track_edit_basetab import _BaseTab
from src.track.edit.track_edit_fieldform import FieldFormTab


class AdvancedTab(_BaseTab):
    """The Advanced fields above an action toolbar and a status line."""

    # Emitted after each track finishes analysis, so the parent dialog can
    # refresh other tabs (e.g. Properties, which holds bpm/key/gain/peak).
    tracks_analyzed = Signal()

    def __init__(self, tracks: list, controller, parent=None, dialog=None):
        super().__init__(tracks, controller, parent)

        # The owning TrackEditDialog — needed so "Delete Track(s)" can close
        # the dialog (which fires `accepted`, reloading the parent view).
        self._dialog = dialog
        self._inner = FieldFormTab("Advanced", tracks, controller)
        self._inner.changed.connect(self.changed)
        self._scheduler: BatchAnalysisScheduler | None = None
        self._metadata_writer = MetadataWriter(controller)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._inner, stretch=1)
        layout.addWidget(self._build_toolbar())

    # ── Toolbar construction ──────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        """Build the action buttons and the status line."""
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

        self._write_btn = QPushButton("Write Metadata to File")
        self._write_btn.setToolTip("Compare each track's file tags to the database and write\nany tags that differ. Tracks already in sync are skipped.")
        self._write_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._write_btn.clicked.connect(self._on_write_metadata)

        self._analyze_btn = QPushButton("Analyze Audio")
        self._analyze_btn.setToolTip("Run audio analysis on the selected track(s).")
        self._analyze_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._analyze_btn.clicked.connect(self._on_analyze)

        delete_label = "Delete Tracks" if self.is_multi else "Delete Track"
        self._delete_btn = QPushButton(delete_label)
        self._delete_btn.setToolTip("Remove the track(s) being edited from the library, optionally\ndeleting the audio file(s) from disk. Closes this dialog.")
        self._delete_btn.setProperty("danger", True)
        self._delete_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._delete_btn.clicked.connect(self._on_delete)

        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._write_btn)
        btn_row.addWidget(self._analyze_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._delete_btn)

        # Status label — hidden until analysis starts
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignLeft)
        self._status_label.hide()

        vbox.addLayout(btn_row)
        vbox.addWidget(self._status_label)
        return container

    # ── _BaseTab protocol ─────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        """Fill the inner field form from `tracks`."""
        self._inner.load(tracks)

    def collect_changes(self) -> dict:
        """Return the inner field form's changes."""
        return self._inner.collect_changes()

    def pending_changes(self) -> set[str]:
        """Return the inner field form's pending fields."""
        return self._inner.pending_changes()

    def refresh_values(self, tracks: list) -> None:
        """Show fresh values without touching unsaved edits."""
        self.tracks = tracks
        self._inner.refresh_values(tracks)

    # ── Copy to clipboard ─────────────────────────────────────────────────

    def _on_copy(self):
        """Serialise current Advanced field values and put them on the clipboard."""
        try:
            values = self._inner.collect_all_values()
            if not values:
                show_status_message(self, "No Advanced fields have values to copy.")
                return

            lines = [f"{field}: {value}" for field, value in values.items()]
            QApplication.clipboard().setText("\n".join(lines))
            logger.info(f"AdvancedTab: copied {len(lines)} field(s) to clipboard")
        except (RuntimeError, ValueError, TypeError) as e:
            logger.error(f"AdvancedTab clipboard copy failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Copy Error", f"Failed to copy:\n{e}")

    # ── Write metadata to file ────────────────────────────────────────────

    def _on_write_metadata(self):
        """Write the database tags that differ to each track's file."""
        try:
            results = [(track, self._metadata_writer.sync_metadata_to_track(track.track_id)) for track in self.tracks]

            updated = [(t, r) for t, r in results if r["success"] and r["changed"]]
            unchanged = [(t, r) for t, r in results if r["success"] and not r["changed"]]
            failed = [(t, r) for t, r in results if not r["success"]]

            self._set_status(f"Write Metadata: {len(updated)} updated, {len(unchanged)} already up to date, {len(failed)} failed.")
            logger.info(f"AdvancedTab: write metadata — {len(updated)} updated, {len(unchanged)} unchanged, {len(failed)} failed")

            if len(results) == 1:
                track, result = results[0]
                if not result["success"]:
                    QMessageBox.warning(self, "Write Metadata to File", result["message"])
                elif not result["changed"]:
                    show_status_message(self, "File tags already match the database.")
                else:
                    show_status_message(self, "Updated tag(s):\n" + "\n".join(result["changed"]))
                return

            lines = [f"Updated: {len(updated)}", f"Already up to date: {len(unchanged)}"]
            if failed:
                lines.append(f"Failed: {len(failed)}")
                for track, result in failed:
                    lines.append(f"  {track.track_name}: {result['message']}")
            show_status_message(self, "\n".join(lines))

        except RuntimeError as e:
            logger.error(f"AdvancedTab write metadata failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Write Metadata Error", f"Failed to write:\n{e}")

    # ── Delete track(s) ──────────────────────────────────────────────────

    def _on_delete(self):
        """Delete the edited track(s), optionally with their files, then close the dialog."""
        count = len(self.tracks)
        names = ", ".join((t.track_name or f"ID {t.track_id}") for t in self.tracks[:3])
        if count > 3:
            names += f" … and {count - 3} more"

        choice = confirm_delete_with_file_option(self, "Delete Tracks", f"Delete {count} track(s)?\n\n{names}")
        if choice is None:
            return

        delete_files = choice == "db_and_file"

        # Collect file paths BEFORE the DB delete — ORM objects go stale after.
        file_paths = []
        if delete_files:
            for track in self.tracks:
                fp = getattr(track, "track_file_path", None)
                if fp:
                    file_paths.append(fp)

        try:
            entity_ids = [track.track_id for track in self.tracks]
            ok = self.controller.delete.delete_entity("Track", entity_ids=entity_ids)
        except (RuntimeError, ValueError, TypeError) as e:
            logger.error(f"AdvancedTab delete failed: {e}", exc_info=True)
            QMessageBox.critical(self, "Delete Error", f"Failed to delete track(s):\n{e}")
            return

        if ok:
            logger.info(f"AdvancedTab: deleted {count} track(s) from DB")
        else:
            logger.error("AdvancedTab: delete_entity returned False for track(s)")
            QMessageBox.warning(self, "Delete Track(s)", "The track(s) could not be removed from the library. See the log for details.")
            return

        if delete_files and file_paths:
            removed = 0
            failed_paths = []
            for fp in file_paths:
                try:
                    if self.controller.delete.delete_file(file_path=fp):
                        removed += 1
                    else:
                        failed_paths.append(fp)
                except OSError as e:
                    logger.error(f"Error deleting file {fp}: {e}")
                    failed_paths.append(fp)
            logger.info(f"AdvancedTab: removed {removed}/{len(file_paths)} file(s) from disk")
            if failed_paths:
                QMessageBox.warning(
                    self,
                    "Some Files Not Deleted",
                    f"{len(failed_paths)} of {len(file_paths)} file(s) could not be "
                    "deleted from disk (e.g. permission denied or already removed). "
                    "The library entries were still removed.\n\n" + "\n".join(failed_paths),
                )

        # Close the dialog; `accepted` triggers the parent view's reload.
        if self._dialog is not None:
            self._dialog.accept()
        else:
            self.window().close()

    # ── Audio analysis ────────────────────────────────────────────────────

    def _on_analyze(self):
        """Start (or stop) a fresh audio analysis of the edited track(s)."""
        if self._scheduler and self._scheduler.is_running:
            # Button acts as a stop button while a run is in progress
            self._scheduler.stop()
            self._analyze_btn.setText("Analyze Audio")
            self._set_status("Analysis stopped.")
            return

        self._scheduler = BatchAnalysisScheduler(self.controller)

        # Wire signals — all delivered on the main thread via Qt's queued
        # connection, so it's safe to touch widgets directly in these slots.
        self._scheduler.signals.track_done.connect(self._on_track_done)
        self._scheduler.signals.batch_done.connect(self._on_batch_done)
        self._scheduler.signals.all_done.connect(self._on_all_done)
        self._scheduler.signals.error.connect(self._on_analysis_error)

        self._analyze_btn.setText("Stop Analysis")
        self._set_status(f"Queuing {len(self.tracks)} track(s)…")
        self._scheduler.start(self.tracks, ignore_cache=True)

    # ── Scheduler signal handlers ─────────────────────────────────────────

    @Slot(int, dict)
    def _on_track_done(self, track_id: int, metadata: dict):
        """Copy one track's analysis results onto the in-memory track and refresh."""
        # The worker already wrote `metadata` to the database.
        track = next((t for t in self.tracks if t.track_id == track_id), None)
        if track is not None:
            for field_name, value in metadata.items():
                setattr(track, field_name, value)

        if self._scheduler:
            done, total = self._scheduler.progress
            self._set_status(f"Analyzed {done} / {total} track(s)…")

        self.tracks_analyzed.emit()

    @Slot(int, int)
    def _on_batch_done(self, completed: int, total: int):
        """Show batch progress."""
        self._set_status(f"Analyzed {completed} / {total} track(s)…")

    @Slot(int)
    def _on_all_done(self, total: int):
        """Reset the button and show the final count."""
        self._analyze_btn.setText("Analyze Audio")
        self._set_status(f"Analysis complete — {total} track(s) processed.")
        logger.info(f"AdvancedTab: analysis finished ({total} track(s))")

    @Slot(int, str)
    def _on_analysis_error(self, track_id: int, message: str):
        """Log a per-track analysis error and show it in the status line."""
        logger.error(f"AdvancedTab: analysis error for track {track_id}: {message}")
        # Don't interrupt the run with a modal — just update the status label.
        # Fatal errors will surface in the all_done summary above.
        self._set_status(f"Error on track {track_id} — see log for details.")

    def cleanup(self) -> None:
        """Disconnect and stop a running analysis before the tab is destroyed."""
        scheduler, self._scheduler = self._scheduler, None
        if scheduler is None:
            return
        for signal, slot in (
            (scheduler.signals.track_done, self._on_track_done),
            (scheduler.signals.batch_done, self._on_batch_done),
            (scheduler.signals.all_done, self._on_all_done),
            (scheduler.signals.error, self._on_analysis_error),
        ):
            with suppress(RuntimeError, TypeError):  # already disconnected
                signal.disconnect(slot)
        if scheduler.is_running:
            scheduler.stop()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        """Show `text` in the status line."""
        self._status_label.setText(text)
        self._status_label.show()
