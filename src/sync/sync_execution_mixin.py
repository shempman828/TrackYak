"""SyncExecutionMixin: start and cancel SyncWorker and show its progress and results."""

import copy

from PySide6.QtWidgets import QMessageBox

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.sync.device_card import plural
from src.sync.sync_selection_mixin import selection_description
from src.sync.sync_worker import SyncWorker


class SyncExecutionMixin:
    """Run SyncWorker for SyncView and render its progress into the bottom bar and the Activity page."""

    # Host provides: current_profile, selected_items, _selection_totals, sync_manager, sync_worker,
    # status_manager, sync_log, activity, progress_bar, current_action, _set_sync_ui_state(idle),
    # _show_sync_result(text, tone); optionally _update_cache_button_label().

    # -----------------------------------------------------------------------
    # Sync execution
    # -----------------------------------------------------------------------

    def _start_sync(self):
        """Confirm the run with the user, then start a SyncWorker for the current profile."""
        if not self.current_profile or not self.selected_items:
            return

        profile = self.current_profile
        if profile.is_mtp:
            dest_name = profile.device_name or profile.device_uri
            dest_desc = f"Device: {dest_name}\nMusic folder: {profile.music_path}"
        else:
            if not profile.path:
                show_status_message(self, "No destination folder set.")
                return
            dest_name = profile.path
            dest_desc = f"Folder: {profile.path}"

        # The de-duplicated total the bottom bar shows, so both numbers agree.
        total_tracks = self._selection_totals[0]
        n_playlists = sum(1 for it in self.selected_items if it["kind"] == "playlist")
        n_moods = sum(1 for it in self.selected_items if it["kind"] == "mood")
        selection_text = selection_description(n_playlists, n_moods)
        clear = profile.clear_before_sync

        confirm_msg = f"Sync {selection_text} ({plural(total_tracks, 'track')}) to:\n\n{dest_desc}"
        if clear:
            confirm_msg += "\n\n⚠️  Destination will be cleared first."
        elif profile.prune_untracked:
            confirm_msg += "\n\n⚠️  Files from playlists/moods no longer in this profile will be removed from the destination."
        if profile.transcode_to_mp3:
            confirm_msg += f"\n\nLossless files will be converted to {profile.transcode_bitrate} MP3."

        reply = QMessageBox.question(self, "Confirm Sync", confirm_msg, QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        logger.info(f"Starting sync for profile '{profile.name}': {selection_text} ({total_tracks} tracks) -> {dest_desc}")
        self.status_manager.start_task(f"Starting sync: {profile.name}")
        self._set_sync_ui_state(False)
        self.progress_bar.setValue(0)
        self.current_action.setText("Preparing…")
        self.activity.begin(dest_name)
        self.sync_log.clear()
        self.sync_log.append(f"Starting sync → {dest_desc}")
        if clear:
            self.sync_log.append("⚠️  Clearing destination first…")

        self._prune_removed = 0
        self._sync_error = None
        # A snapshot: option edits made while the run is going must not change the running sync.
        self.sync_worker = SyncWorker(self.sync_manager, list(self.selected_items), copy.deepcopy(profile))
        self.sync_worker.progress.connect(self._on_sync_progress)
        self.sync_worker.playlist_complete.connect(self._on_playlist_complete)
        self.sync_worker.prune_complete.connect(self._on_prune_complete)
        self.sync_worker.notice.connect(self._on_sync_notice)
        self.sync_worker.failed.connect(self._on_sync_failed)
        self.sync_worker.sync_finished.connect(self._on_sync_finished)
        self.sync_worker.start()

    def _cancel_sync(self):
        """Ask, then request cooperative cancellation of the running sync."""
        if self.sync_worker and self.sync_worker.isRunning():
            reply = QMessageBox.question(self, "Cancel Sync", "Cancel the running sync?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes and self.sync_worker.isRunning():
                self.sync_worker.cancel()
                logger.info(f"Sync cancelled by user for profile '{self.sync_worker.profile.name}'")
                self.sync_log.append("*** Sync cancelled by user ***")
                self.current_action.setText("Cancelling…")

    # -----------------------------------------------------------------------
    # Sync signal handlers
    # -----------------------------------------------------------------------

    def _on_sync_progress(self, current: int, total: int, message: str):
        """Show the current step and overall percentage."""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            pct = f"{current / total * 100:.0f}%"
            self.current_action.setText(f"{message}  ·  {pct}")
            self.status_manager.show_message(f"Syncing: {message}  ({pct})", 0)
        else:
            self.current_action.setText(message)
            self.status_manager.show_message(f"Syncing: {message}", 0)

    def _on_playlist_complete(self, result: dict):
        """Log one playlist's result and add it to the Activity page."""
        icon = "✅" if result["success"] else "❌"
        skipped = result.get("tracks_skipped", 0)
        failed = result.get("tracks_failed", 0)
        transcoded = result.get("tracks_transcoded", 0)
        notes = []
        if skipped:
            notes.append(f"{skipped} already there")
        if transcoded:
            notes.append(f"{transcoded} to MP3")
        if failed:
            notes.append(f"{failed} failed")
        note = f"  ({', '.join(notes)})" if notes else ""
        self.sync_log.append(f"{icon} {result['playlist_name']}: {result['message']}{note}")
        for failure in result.get("failures", []):
            self.sync_log.append(f"      ✗ {failure['artist']} — {failure['title']}: {failure['reason']}")
        self._scroll_log_to_end()
        self.activity.add_result(result)

    def _on_prune_complete(self, result: dict):
        """Log and show the files the prune pass removed."""
        removed = result.get("removed_tracks", []) + result.get("removed_playlists", [])
        self._prune_removed = len(removed)
        if not removed:
            return
        self.sync_log.append(f"🗑  Removed {len(removed)} file(s) no longer in this profile")
        for name in removed:
            self.sync_log.append(f"      - {name}")
        self._scroll_log_to_end()
        self.activity.add_removed(removed)

    def _on_sync_notice(self, message: str):
        """Log a non-fatal problem the worker reported."""
        self.sync_log.append(f"⚠️  {message}")
        self._scroll_log_to_end()

    def _on_sync_failed(self, message: str):
        """Remember the error that stopped the run; _on_sync_finished reports it."""
        self._sync_error = message
        self.sync_log.append(f"✗ Sync stopped by an error: {message}")
        self._scroll_log_to_end()

    def _on_sync_finished(self, results: list[dict]):
        """Summarize the run in the log, the Activity headline, the bottom bar and the status bar."""
        self._set_sync_ui_state(True)

        successful = sum(1 for r in results if r["success"])
        total = len(results)
        total_copied = sum(r.get("tracks_copied", 0) for r in results)
        total_skipped = sum(r.get("tracks_skipped", 0) for r in results)
        total_failed = sum(r.get("tracks_failed", 0) for r in results)
        total_transcoded = sum(r.get("tracks_transcoded", 0) for r in results)
        removed = getattr(self, "_prune_removed", 0)
        error = getattr(self, "_sync_error", None)
        failed_note = f", {total_failed} failed" if total_failed else ""
        mp3_note = f", {total_transcoded} to MP3" if total_transcoded else ""
        removed_note = f", {removed} removed" if removed else ""
        extra = f"{mp3_note}{failed_note}{removed_note}"
        cancelled = self.sync_worker is not None and self.sync_worker.is_cancelled

        logger.info(f"Sync finished: {successful}/{total} playlists succeeded, {total_copied} tracks copied, {total_skipped} skipped{extra}")
        self.sync_log.append(f"\n=== Sync complete: {successful}/{total} playlists  |  {total_copied} copied, {total_skipped} skipped{extra} ===")

        # One short line for the bottom bar and the Activity headline.
        bits = [f"{total_copied:,} copied"]
        if total_failed:
            bits.append(f"{total_failed:,} failed")
        if removed:
            bits.append(f"{removed:,} removed")
        counts = " · ".join(bits)
        if error:
            headline, tone = f"Sync failed — {error}", "error"
        elif cancelled:
            headline, tone = f"Sync cancelled — {counts}", "warn"
        elif successful == 0:
            headline, tone = "Nothing was copied — check that the source files exist and the destination is writable", "error"
        elif total_failed or successful < total:
            headline, tone = f"Synced with problems — {counts}", "warn"
        else:
            headline, tone = f"Sync complete — {counts}", "ok"
        self.activity.finish(headline, tone)
        self._show_sync_result(headline, tone)

        if error:
            self.status_manager.end_task(f"Sync error: {error}", 5000)
        elif cancelled:
            self.status_manager.end_task("Sync cancelled", 3000)
        elif successful > 0:
            self.status_manager.end_task(f"Sync complete: {total_copied} copied, {total_skipped} skipped{failed_note}{removed_note}", 5000)
        else:
            self.status_manager.end_task("Sync completed — no tracks copied", 3000)

        # A run that converted to MP3 changed the cache size shown on the Clear button.
        update_cache_label = getattr(self, "_update_cache_button_label", None)
        if update_cache_label is not None:
            update_cache_label()

    def _scroll_log_to_end(self):
        """Keep the newest log line in view."""
        bar = self.sync_log.verticalScrollBar()
        bar.setValue(bar.maximum())
