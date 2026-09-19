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
    """Owns background smart-playlist-refresh workers for a PlaylistView.

    Holds a back-reference to the view for the handful of things a refresh
    callback needs from it (reload the tree, emit playlist_updated, and act
    as the QWidget parent for status messages/dialogs) instead of threading
    each of those through as separate constructor args.
    """

    def __init__(self, view) -> None:
        self.view = view
        self.builder = SmartPlaylistBuilder(view.controller)
        # Keep references to in-flight refresh workers so they aren't
        # garbage-collected mid-run; keyed by playlist_id.
        self._refresh_workers: dict[int, _SmartPlaylistRefreshWorker] = {}

    def start_refresh(self, playlist_id: int, on_finished) -> None:
        """Launch a background refresh for ``playlist_id``, off the UI
        thread, so re-evaluating criteria against a large library can't
        block the UI or starve other threads (e.g. audio playback).

        ``on_finished(success, playlist_id)`` runs on the UI thread once
        the worker completes.
        """
        if playlist_id in self._refresh_workers:
            # A refresh for this playlist is already running — let it finish
            # rather than starting a second one against the same rows.
            return

        # Large libraries can make this take a moment -- tell the user
        # something is happening instead of leaving the UI looking idle.
        show_status_message(self.view, "Refreshing playlist…")

        worker = _SmartPlaylistRefreshWorker(self.builder, playlist_id, parent=self.view)

        def _handle_finished(success: bool, pid: int) -> None:
            self._refresh_workers.pop(pid, None)
            on_finished(success, pid)

        worker.finished.connect(_handle_finished)
        self._refresh_workers[playlist_id] = worker
        worker.start()

    def refresh_smart_playlist(self, playlist_id: int) -> None:
        """Refresh a smart playlist and show the user a result message."""
        self.start_refresh(playlist_id, self.on_manual_playlist_refreshed)

    def on_created_playlist_refreshed(self, success: bool, playlist_id: int, name: str) -> None:
        # Whether or not the initial match found tracks, the playlist and
        # its criteria already exist — always refresh the UI to show it.
        self.view.load_playlists()
        self.view.playlist_updated.emit()
        if success:
            logger.info(f"Created new smart playlist: {name}")
        else:
            logger.error(f"Created smart playlist '{name}' but initial refresh failed")

    def on_edited_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        if success:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
        else:
            QMessageBox.warning(
                self.view,
                "Refresh Failed",
                "Criteria were saved, but the track list could not be updated. "
                "Try right-clicking the playlist and choosing Refresh.",
            )

    def on_manual_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        if success:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
            # Read the count directly from the playlist object — no extra DB call needed
            try:
                playlist_obj = self.view.controller.get.get_entity_object(
                    "Playlist", playlist_id=playlist_id
                )
                count = getattr(playlist_obj, "track_count", None)
                msg = (
                    f"Done! The playlist now contains {count} matching track(s)."
                    if count is not None
                    else "Playlist updated successfully."
                )
            except SQLAlchemyError as e:
                logger.warning(f"Could not load updated playlist track count: {e}")
                msg = "Playlist updated successfully."
            show_status_message(self.view, msg)
        else:
            QMessageBox.warning(
                self.view,
                "Refresh Failed",
                "Could not refresh the playlist. Check the log for details.",
            )

    def on_startup_playlist_refreshed(self, success: bool, playlist_id: int) -> None:
        # Quiet on success/failure -- a popup for a background startup
        # refresh the user didn't ask for would just be noise.
        if success:
            self.view.load_playlists()
            self.view.playlist_updated.emit()
        else:
            logger.error(f"Auto-refresh failed for smart playlist {playlist_id}")
