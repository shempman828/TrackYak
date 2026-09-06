from PySide6.QtWidgets import QMessageBox

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.sync.sync_worker import SyncWorker


class SyncExecutionMixin:
    """
    Kicks off SyncWorker, wires its signals, and renders progress/results
    into the Log tab for SyncView.

    Expects the host class to provide: self.current_profile,
    self.selected_items, self.tabs, self.sync_manager, self.sync_worker,
    self.status_manager, self.sync_log, self.progress_bar, self.sync_btn,
    self.cancel_sync_btn, self.add_profile_btn, self.current_action.
    """

    # -----------------------------------------------------------------------
    # Sync execution
    # -----------------------------------------------------------------------

    def _start_sync(self):
        if not self.current_profile or not self.selected_items:
            return

        # Validate destination
        if self.current_profile.is_mtp:
            if not self.current_profile.device_uri:
                show_status_message(self, "No device linked.")
                return
            name = self.current_profile.device_name or self.current_profile.device_uri
            dest_desc = f"Device: {name}\nMusic folder: {self.current_profile.music_path}"
        else:
            if not self.current_profile.path:
                show_status_message(self, "No destination folder set.")
                return
            dest_desc = f"Folder: {self.current_profile.path}"

        total_tracks = sum(p["track_count"] for p in self.selected_items)
        n_playlists = sum(1 for it in self.selected_items if it["kind"] == "playlist")
        n_moods = sum(1 for it in self.selected_items if it["kind"] == "mood")
        selection_parts = []
        if n_playlists:
            selection_parts.append(f"{n_playlists} playlist(s)")
        if n_moods:
            selection_parts.append(f"{n_moods} mood(s)")
        clear = self.current_profile.clear_before_sync

        confirm_msg = (
            f"Sync {' + '.join(selection_parts)} ({total_tracks} tracks) to:\n\n{dest_desc}"
        )
        if clear:
            confirm_msg += "\n\n⚠️  Destination will be cleared first."
        elif self.current_profile.prune_untracked:
            confirm_msg += (
                "\n\n⚠️  Files from playlists/moods no longer in this profile "
                "will be removed from the destination."
            )
        if self.current_profile.transcode_to_mp3:
            bitrate = self.current_profile.transcode_bitrate
            confirm_msg += f"\n\nLossless files will be converted to {bitrate} MP3."

        reply = QMessageBox.question(
            self, "Confirm Sync", confirm_msg, QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Switch to Log tab so the user can see progress
        self.tabs.setCurrentIndex(2)

        logger.info(
            f"Starting sync for profile '{self.current_profile.name}': "
            f"{' + '.join(selection_parts)} ({total_tracks} tracks) -> {dest_desc}"
        )
        self.status_manager.start_task(f"Starting sync: {self.current_profile.name}")
        self._set_sync_ui_state(False)
        self.progress_bar.setVisible(True)
        self.sync_log.clear()
        self.sync_log.append(f"Starting sync → {dest_desc}")
        if clear:
            self.sync_log.append("⚠️  Clearing destination first…")

        self._prune_removed = 0
        self.sync_worker = SyncWorker(self.sync_manager, self.selected_items, self.current_profile)
        self.sync_worker.progress.connect(self._on_sync_progress)
        self.sync_worker.playlist_complete.connect(self._on_playlist_complete)
        self.sync_worker.prune_complete.connect(self._on_prune_complete)
        self.sync_worker.finished.connect(self._on_sync_finished)
        self.sync_worker.start()

    def _cancel_sync(self):
        if self.sync_worker and self.sync_worker.isRunning():
            reply = QMessageBox.question(
                self, "Cancel Sync", "Cancel the running sync?", QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.sync_worker.cancel()
                logger.info(f"Sync cancelled by user for profile '{self.current_profile.name}'")
                self.sync_log.append("*** Sync cancelled by user ***")
                self.status_manager.end_task("Sync cancelled", 3000)

    def _set_sync_ui_state(self, idle: bool):
        self.sync_btn.setVisible(idle)
        self.cancel_sync_btn.setVisible(not idle)
        self.add_profile_btn.setEnabled(idle)

    # -----------------------------------------------------------------------
    # Sync signal handlers
    # -----------------------------------------------------------------------

    def _on_sync_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        self.current_action.setText(message)
        pct = f"  ({current / total * 100:.0f}%)" if total > 0 else ""
        self.status_manager.show_message(f"Syncing: {message}{pct}", 0)

    def _on_playlist_complete(self, result: dict):
        icon = "✅" if result["success"] else "❌"
        skipped = result.get("tracks_skipped", 0)
        failed = result.get("tracks_failed", 0)
        transcoded = result.get("tracks_transcoded", 0)
        notes = []
        if skipped:
            notes.append(f"{skipped} duplicates skipped")
        if transcoded:
            notes.append(f"{transcoded} to MP3")
        if failed:
            notes.append(f"{failed} failed")
        note = f"  ({', '.join(notes)})" if notes else ""
        self.sync_log.append(f"{icon} {result['playlist_name']}: {result['message']}{note}")
        for failure in result.get("failures", []):
            self.sync_log.append(
                f"      ✗ {failure['artist']} — {failure['title']}: {failure['reason']}"
            )
        self.sync_log.verticalScrollBar().setValue(self.sync_log.verticalScrollBar().maximum())

    def _on_prune_complete(self, result: dict):
        removed = result.get("removed_tracks", []) + result.get("removed_playlists", [])
        self._prune_removed = len(removed)
        if not removed:
            return
        self.sync_log.append(f"🗑  Removed {len(removed)} file(s) no longer in this profile")
        for name in removed:
            self.sync_log.append(f"      - {name}")
        self.sync_log.verticalScrollBar().setValue(self.sync_log.verticalScrollBar().maximum())

    def _on_sync_finished(self, results: list[dict]):
        self._set_sync_ui_state(True)
        self.progress_bar.setVisible(False)

        successful = sum(1 for r in results if r["success"])
        total = len(results)
        total_copied = sum(r.get("tracks_copied", 0) for r in results)
        total_skipped = sum(r.get("tracks_skipped", 0) for r in results)
        total_failed = sum(r.get("tracks_failed", 0) for r in results)
        total_transcoded = sum(r.get("tracks_transcoded", 0) for r in results)
        removed = getattr(self, "_prune_removed", 0)
        failed_note = f", {total_failed} failed" if total_failed else ""
        mp3_note = f", {total_transcoded} to MP3" if total_transcoded else ""
        removed_note = f", {removed} removed" if removed else ""
        extra = f"{mp3_note}{failed_note}{removed_note}"

        logger.info(
            f"Sync finished: {successful}/{total} playlists succeeded, "
            f"{total_copied} tracks copied, {total_skipped} skipped{extra}"
        )

        self.current_action.setText(
            f"Done — {successful}/{total} playlists  ·  "
            f"{total_copied} copied, {total_skipped} skipped{extra}"
        )
        self.sync_log.append(
            f"\n=== Sync complete: {successful}/{total} playlists  |  "
            f"{total_copied} copied, {total_skipped} skipped{extra} ==="
        )

        if successful > 0:
            self.status_manager.end_task(
                f"Sync complete: {total_copied} copied, "
                f"{total_skipped} skipped{failed_note}{removed_note}",
                5000,
            )
            show_status_message(
                self,
                f"Sync finished! Playlists: {successful}/{total} successful  ·  "
                f"Tracks copied: {total_copied}  ·  Duplicates skipped: {total_skipped}"
                + (f"  ·  Failed: {total_failed}" if total_failed else ""),
                5000,
            )
        else:
            self.status_manager.end_task("Sync completed — no tracks copied", 3000)
            QMessageBox.warning(
                self,
                "Sync Complete",
                "No tracks were copied.\n\n"
                "Check that source files exist and the destination is writable.",
            )
