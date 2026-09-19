"""Review/checkbox dialog for confirming and applying a fetched MusicBrainz release."""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QMessageBox

# File map: track-matching logic lives in AlbumMusicBrainzTrackMatchingMixin
# (album_musicbrainz_track_matching.py); UI construction lives in
# AlbumMusicBrainzReviewUIMixin (album_musicbrainz_review_ui.py); the
# write-phase functions and _ReviewAcceptWorker live in
# album_musicbrainz_review_import.py. This class composes the three and
# owns the accept/cancel orchestration between them.
from src.album.album_musicbrainz_review_import import (
    _batch_update_tracks,
    _plan_discs,
    _ReviewAcceptWorker,
    _track_scalar_update,
)
from src.album.album_musicbrainz_review_ui import AlbumMusicBrainzReviewUIMixin
from src.album.album_musicbrainz_track_matching import AlbumMusicBrainzTrackMatchingMixin
from src.musicbrainz.musicbrainz_artist import MBAlias
from src.musicbrainz.musicbrainz_release import MBLabelInfo, MBReleaseDetail, MBReleaseTrack


class AlbumMusicBrainzReviewDialog(
    AlbumMusicBrainzTrackMatchingMixin, AlbumMusicBrainzReviewUIMixin, QDialog
):
    def __init__(
        self, controller, album, detail: MBReleaseDetail, aliases: list[MBAlias], parent=None
    ):
        super().__init__(parent)
        self.controller = controller
        self.album = album
        self.detail = detail
        self.aliases = aliases
        self.has_content = False

        self._matched: dict[int, Any] = {}  # id(MBReleaseTrack) -> Track
        self._manual_combos: list[tuple[QComboBox, MBReleaseTrack]] = []
        self._alias_checks: list[tuple[QCheckBox, MBAlias]] = []
        self._album_credit_checks: list[tuple[QCheckBox, Any]] = []
        self._label_checks: list[tuple[QCheckBox, MBLabelInfo]] = []
        self._credit_checks: list[tuple[QCheckBox, MBReleaseTrack, Any]] = []
        self._location_checks: list[tuple[QCheckBox, str, list[MBReleaseTrack]]] = []
        self._failed_writes: list[str] = []
        self._accept_worker: _ReviewAcceptWorker | None = None

        self.setWindowTitle("Review MusicBrainz Album Details")
        self.setMinimumSize(560, 540)

        self._match_tracks()
        self._match_summary = f"Matched {len(self._matched)} of {len(detail.tracks)} track(s)."
        self._build_ui()

    # ------------------------------------------------------------------
    # Fill-blank scalars for auto-matched tracks (disc assignment, track
    # number/side/barcode), for the has_content=False path: called directly
    # by the caller (see AlbumMusicBrainzMixin) when there's nothing else to
    # review. When has_content is True, _on_accept()/_ReviewAcceptWorker
    # independently recompute the same fill-blank updates instead of
    # calling this method -- the two paths aren't wired through shared code,
    # so keep them in sync by hand if either changes.
    # ------------------------------------------------------------------

    def apply_immediate_scalars(self):
        disc_by_number, failed = _plan_discs(self.controller, self.album, self.detail)
        updates = []
        for mbt in self.detail.tracks:
            track = self._matched.get(id(mbt))
            if track is None:
                continue
            update = _track_scalar_update(track, mbt, disc_by_number, self.detail.barcode)
            if update is not None:
                updates.append(update)
        failed += _batch_update_tracks(self.controller, updates)
        self._failed_writes.extend(failed)
        self._report_failed_writes()

    def _report_failed_writes(self):
        if not self._failed_writes:
            return
        QMessageBox.warning(
            self,
            "Some MusicBrainz Data Could Not Be Saved",
            "The following item(s) could not be saved and were skipped:\n\n"
            + "\n".join(f"• {item}" for item in self._failed_writes),
        )
        self._failed_writes.clear()

    # ------------------------------------------------------------------
    # Apply -- everything the user gets to review, plus the fill-blank
    # scalars for auto-matched tracks, all happen here, only once OK has
    # actually been clicked. Nothing above this point writes to the
    # database.
    # ------------------------------------------------------------------

    def _on_accept(self):
        """Read every bit of Qt widget state that the write phase needs
        (checked boxes, manual-match combo selections) right here on the UI
        thread, then hand it all to _ReviewAcceptWorker as plain data --
        with enough credits on a release, running the resolve/write loop
        synchronously froze the whole app long enough to trigger the OS
        "not responding" prompt. Nothing below this point may touch a
        QWidget from the worker; see _ReviewAcceptWorker's docstring."""
        matched_track_ids = {mbt_id: track.track_id for mbt_id, track in self._matched.items()}
        manual_track_ids: dict[int, int | None] = {}
        for combo, mbt in self._manual_combos:
            track = combo.currentData()
            manual_track_ids[id(mbt)] = track.track_id if track is not None else None

        checked_aliases = [alias for cb, alias in self._alias_checks if cb.isChecked()]
        checked_labels = [label for cb, label in self._label_checks if cb.isChecked()]
        checked_album_credits = [
            credit for cb, credit in self._album_credit_checks if cb.isChecked()
        ]
        checked_track_credits = [
            (id(mbt), credit) for cb, mbt, credit in self._credit_checks if cb.isChecked()
        ]
        checked_locations = [
            (place_mbid, mb_tracks)
            for cb, place_mbid, mb_tracks in self._location_checks
            if cb.isChecked()
        ]

        self._set_busy(True)
        self._accept_worker = _ReviewAcceptWorker(
            self.controller,
            self.album.album_id,
            self.detail,
            matched_track_ids,
            manual_track_ids,
            checked_aliases,
            checked_labels,
            checked_album_credits,
            checked_track_credits,
            checked_locations,
            parent=self,
        )
        self._accept_worker.progress.connect(self._on_accept_progress)
        self._accept_worker.finished.connect(self._on_accept_finished)
        self._accept_worker.error.connect(self._on_accept_error)
        self._accept_worker.start()

    def _set_busy(self, busy: bool):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(not busy)
        self._scroll.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.progress_status_label.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)  # indeterminate until first progress signal
            self.progress_status_label.setText("Applying MusicBrainz data…")

    def _on_accept_progress(self, current: int, total: int):
        if total == 0:
            return  # nothing to process yet -- stay indeterminate
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_status_label.setText(f"Applying MusicBrainz data… ({current} of {total})")

    def _on_accept_finished(self, failed_writes: list[str]):
        self._set_busy(False)
        self._failed_writes.extend(failed_writes)
        self._report_failed_writes()
        self.accept()

    def _on_accept_error(self, message: str):
        self._set_busy(False)
        QMessageBox.critical(
            self,
            "MusicBrainz Import Failed",
            f"Could not finish importing MusicBrainz data:\n\n{message}",
        )

    def _detach_accept_worker(self):
        """Best-effort stop for a write still in flight when the dialog is
        closed out from under it (Cancel/Esc/X while busy -- the buttons are
        disabled during the write, but Esc still reaches reject()). Mirrors
        _detach_running_worker in musicbrainz_match_dialog.py: request
        cancellation, then disconnect so a signal arriving after this dialog
        is gone can't call back into dead widgets, and reparent so a still-
        running QThread destroyed alongside its parent doesn't crash Qt."""
        worker = self._accept_worker
        if worker is None or not worker.isRunning():
            return
        worker.request_cancel()
        with contextlib.suppress(RuntimeError):
            worker.finished.disconnect()
        with contextlib.suppress(RuntimeError):
            worker.error.disconnect()
        with contextlib.suppress(RuntimeError):
            worker.progress.disconnect()
        worker.setParent(None)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)

    def reject(self):
        self._detach_accept_worker()
        super().reject()

    def closeEvent(self, event):
        self._detach_accept_worker()
        super().closeEvent(event)
