"""
SyncWorker — runs the sync operation in a background thread.

Accepts a SyncProfile and automatically picks MTP or folder sync.
Both paths emit identical signals so the UI is fully agnostic.
"""

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import StatusManager
from src.sync.sync_manager import SyncManager
from src.sync.sync_profile import SyncProfile


class SyncWorker(CancellableWorker):
    progress = Signal(int, int, str)  # current, total, message
    playlist_complete = Signal(dict)  # one playlist result
    finished = Signal(list)  # all results

    def __init__(self, sync_manager: SyncManager, playlists: list[dict], profile: SyncProfile):
        super().__init__()
        self.sync_manager = sync_manager
        self.playlists = playlists
        self.profile = profile
        self.results = []

    def run(self):
        try:
            status_manager = StatusManager
            profile = self.profile

            # ── Optionally clear destination ────────────────────────────────
            if profile.clear_before_sync and not self.is_cancelled:
                self.progress.emit(0, 1, "Clearing destination…")
                if profile.is_mtp:
                    self.sync_manager.clear_mtp_folders(profile.device_uri, profile.music_path)
                else:
                    self.sync_manager.clear_device_folder(profile.path)

            # ── Sync each playlist ──────────────────────────────────────────
            total = len(self.playlists)
            for i, playlist in enumerate(self.playlists):
                if self.is_cancelled:
                    status_manager.show_message("Sync cancelled", 3000)
                    break

                self.progress.emit(i, total, f"Starting: {playlist['name']}")
                status_manager.show_message(f"Syncing: {playlist['name']}", 0)

                if profile.is_mtp:
                    result = self.sync_manager.sync_playlist_to_mtp(
                        playlist,
                        profile.device_uri,
                        profile.music_path,
                        self._progress_callback,
                        should_cancel=lambda: self.is_cancelled,
                        transcode_to_mp3=profile.transcode_to_mp3,
                        transcode_bitrate=profile.transcode_bitrate,
                    )
                else:
                    result = self.sync_manager.sync_playlist_to_device(
                        playlist,
                        profile.path,
                        self._progress_callback,
                        should_cancel=lambda: self.is_cancelled,
                        transcode_to_mp3=profile.transcode_to_mp3,
                        transcode_bitrate=profile.transcode_bitrate,
                    )

                self.results.append(result)
                self.playlist_complete.emit(result)

            self.finished.emit(self.results)

        except Exception as e:
            # Intentional broad boundary catch: this is a QThread run() loop
            # and must not let an exception kill the thread silently — surface
            # it to the UI instead.
            logger.exception("SyncWorker error")
            StatusManager.end_task(f"Sync error: {e!s}", 5000)
            self.finished.emit([])
        finally:
            # sync_manager now resolves its own session lazily per calling
            # thread (see sync_view.py) -- this thread's first DB touch
            # (get_item_tracks, called from sync_playlist_to_*) registered a
            # fresh Session here that nothing else releases.
            self._release_db_session()

    def _progress_callback(self, current: int, total: int, message: str):
        self.progress.emit(current, total, message)

    def cancel(self):
        """Alias for request_cancel() -- kept for sync_view.py's existing call site."""
        self.request_cancel()
