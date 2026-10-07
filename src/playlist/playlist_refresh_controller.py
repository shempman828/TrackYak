"""playlist_refresh_controller.py"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.playlist.playlist_smart_builder import SmartPlaylistBuilder


class _SmartPlaylistRefreshWorker(CancellableWorker):
    """Runs SmartPlaylistBuilder.refresh_playlist() off the UI thread."""

    finished = Signal(bool, int)  # success, playlist_id

    def __init__(self, builder: SmartPlaylistBuilder, playlist_id: int, parent=None):
        super().__init__(parent)
        self._builder = builder
        self._playlist_id = playlist_id

    def run(self) -> None:
        """Refresh the playlist and always emit `finished`."""
        # Must always emit `finished`, even on an unexpected error -- the
        # caller keys an in-flight-refresh guard off this signal, and a
        # worker that dies silently would leave that playlist_id stuck
        # "refreshing" forever.
        try:
            success = self._builder.refresh_playlist(self._playlist_id)
        except Exception:
            logger.exception(f"Unexpected error refreshing playlist {self._playlist_id}")
            success = False
        finally:
            self._release_db_session()
        self.finished.emit(success, self._playlist_id)


class PlaylistRefreshController:
    """Owns background smart-playlist-refresh workers for a PlaylistView."""

    # Holds a back-reference to the view to reload the tree, emit
    # playlist_updated, and parent status messages/dialogs.

    def __init__(self, view) -> None:
        """Bind the controller to `view` and its DB controller."""
        self.view = view
        self.builder = SmartPlaylistBuilder(view.controller)
        # Keep references to in-flight refresh workers so they aren't
        # garbage-collected mid-run; keyed by playlist_id.
        self._refresh_workers: dict[int, _SmartPlaylistRefreshWorker] = {}
        # One queued re-run per playlist, requested while a refresh was running.
        self._pending_reruns: dict[int, object] = {}
        # Startup refreshes still running; the tree reloads once when this empties.
        self._startup_pending: set[int] = set()

    def start_refresh(self, playlist_id: int, on_finished) -> None:
        """Refresh ``playlist_id`` off the UI thread, then call ``on_finished(success, playlist_id)``."""
        if playlist_id in self._refresh_workers:
            # Re-run once the current refresh ends -- criteria may have just
            # changed, so the running result could already be stale.
            self._pending_reruns[playlist_id] = on_finished
            return

        # Large libraries can make this take a moment -- tell the user
        # something is happening instead of leaving the UI looking idle.
        show_status_message(self.view, "Refreshing playlist…")

        worker = _SmartPlaylistRefreshWorker(self.builder, playlist_id, parent=self.view)

        def _handle_finished(success: bool, pid: int) -> None:
            self._refresh_workers.pop(pid, None)
            rerun = self._pending_reruns.pop(pid, None)
            if rerun is not None:
                # Skip this now-stale result; the re-run reports instead.
                self.start_refresh(pid, rerun)
                return
            self.view.reload_playlist_window(pid)
            on_finished(success, pid)

        worker.finished.connect(_handle_finished)
        self._refresh_workers[playlist_id] = worker
        worker.start()

    def refresh_smart_playlist(self, playlist_id: int) -> None:
        """Refresh a smart playlist and show the user a result message."""
        self.start_refresh(playlist_id, self.on_manual_playlist_refreshed)

    def refresh_on_startup(self, playlist_ids: list[int]) -> None:
        """Refresh each auto-refresh playlist, reloading the tree once at the end."""
        self._startup_pending.update(playlist_ids)
        for playlist_id in playlist_ids:
            self.start_refresh(playlist_id, self.on_startup_playlist_refreshed)

    def on_created_playlist_refreshed(self, success: bool, playlist_id: int, name: str) -> None:
        """Show a new smart playlist after its first refresh."""
        # Whether or not the initial match found tracks, the playlist and
        # its criteria already exist — always refresh the UI to show it.
        self.view.load_playlists()
        self.view.playlist_updated.emit()
        if success:
            logger.info(f"Created new smart playlist: {name}")
        else:
            logger.error(f"Created smart playlist '{name}' but initial refresh failed")
            show_status_message(self.view, f"Created '{name}', but its tracks could not be loaded. Try Refresh Playlist.")

    def on_edited_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        """Reload after an edit's refresh, or warn that the track list is stale."""
        if success:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
        else:
            QMessageBox.warning(self.view, "Refresh Failed", "Criteria were saved, but the track list could not be updated. Try right-clicking the playlist and choosing Refresh.")

    def on_manual_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        """Reload and report the new track count after a user-requested refresh."""
        if success:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
            # Read the count directly from the playlist object — no extra DB call needed
            try:
                playlist_obj = self.view.controller.get.get_entity_object("Playlist", playlist_id=playlist_id)
                count = getattr(playlist_obj, "track_count", None)
                msg = f"Done! The playlist now contains {count} matching track(s)." if count is not None else "Playlist updated successfully."
            except SQLAlchemyError as e:
                logger.warning(f"Could not load updated playlist track count: {e}")
                msg = "Playlist updated successfully."
            show_status_message(self.view, msg)
        else:
            QMessageBox.warning(self.view, "Refresh Failed", "Could not refresh the playlist. Check the log for details.")

    def on_startup_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        """Reload the tree once, after the last startup refresh ends."""
        # Quiet on success/failure -- a popup for a background startup
        # refresh the user didn't ask for would just be noise.
        if not success:
            logger.error(f"Auto-refresh failed for smart playlist {playlist_id}")
        self._startup_pending.discard(playlist_id)
        if not self._startup_pending:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
