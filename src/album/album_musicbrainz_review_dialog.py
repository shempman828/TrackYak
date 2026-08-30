"""
album_musicbrainz_review_dialog.py

Review/checkbox dialog shown after a MusicBrainz canonical release has been
fetched in full (see musicbrainz_release.fetch_release_detail). Modeled on
src/artist/artist_enrichment_review_dialog.py's checkbox-per-item /
apply-on-accept pattern, scaled up to per-track groups: album credits, track
credits, and recording locations are relational data that needs
find-or-create/dedup against existing local data before it's safe to write,
so the user confirms what gets imported rather than it happening silently.

Nothing is written until the user actually clicks OK: fill-blank scalars
(track number/side/barcode, disc assignment) for auto-matched tracks are
computed at construction time but only applied in `_on_accept()`, right
alongside the judgment-call items (manual track matches, credits, recording
locations, album aliases) -- so hitting Cancel here leaves the database
untouched, not just the checkbox-gated portion of it.

Track-matching logic lives in AlbumMusicBrainzTrackMatchingMixin
(album_musicbrainz_track_matching.py) and UI construction lives in
AlbumMusicBrainzReviewUIMixin (album_musicbrainz_review_ui.py); the
write-phase functions and _ReviewAcceptWorker live in
album_musicbrainz_review_import.py. This class composes the three and
owns the accept/cancel orchestration between them.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QMessageBox

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
    # number/side/barcode). Computed against self._matched at construction
    # time, but the actual writes are deferred to _on_accept() below --
    # nothing here touches the database until the user clicks OK.
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
        try:
            worker.finished.disconnect()
        except RuntimeError:
            pass
        try:
            worker.error.disconnect()
        except RuntimeError:
            pass
        try:
            worker.progress.disconnect()
        except RuntimeError:
            pass
        worker.setParent(None)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)

    def reject(self):
        self._detach_accept_worker()
        super().reject()

    def closeEvent(self, event):
        self._detach_accept_worker()
        super().closeEvent(event)
